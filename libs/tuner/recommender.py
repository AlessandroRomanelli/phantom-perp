"""Recommendation validation pipeline and tuning cycle orchestrator.

Wires Claude's output through bounds enforcement, type coercion, atomic YAML
writes, and audit logging. Enforces CLAI-04: every recommendation is validated
against bounds before being applied.

Implements D-18: load data -> call Claude -> validate -> apply -> audit.
This is the function Phase 14's Docker entrypoint will invoke.

Exports:
    TuningResult -- frozen dataclass with summary and list of applied changes
    validate_recommendation -- validates and clips a single Claude recommendation
    run_tuning_cycle -- top-level orchestration function
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from libs.common.config import load_strategy_config
from libs.metrics.engine import StrategyMetrics, compute_strategy_metrics
from libs.storage.repository import AttributedFill
from libs.tuner.audit import ParameterChange, log_no_change, log_parameter_change
from libs.tuner.bounds import BoundsEntry, clip_value, load_bounds_registry
from libs.tuner.claude_client import (
    DEFAULT_MODEL,
    build_system_prompt,
    build_user_message,
    call_claude,
)
from libs.tuner.writer import apply_parameter_changes

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TuningResult:
    """Result of a complete tuning cycle run.

    Args:
        summary: Claude's overall assessment string (D-06 -- used by Phase 15 Telegram).
        changes: All ParameterChange records applied during this run.
    """

    summary: str
    changes: list[ParameterChange]


def validate_recommendation(
    rec: dict[str, Any],
    registry: dict[str, BoundsEntry],
) -> float | int | None:
    """Validate a single Claude recommendation against the bounds registry.

    Enforces CLAI-04 pipeline:
    1. Check param is in registry (D-10).
    2. Check value is numeric.
    3. Clip value to bounds; log warning if clipping occurred (D-09).
    4. Round to int if registry type is "int" (D-11).

    Args:
        rec: Recommendation dict with 'param' and 'value' keys.
        registry: Bounds registry from load_bounds_registry().

    Returns:
        Validated (and coerced) value, or None if the recommendation is rejected.
    """
    param = rec.get("param", "")

    # D-10: reject unknown params
    if param not in registry:
        _logger.warning("tuner_unknown_param", param=param, strategy=rec.get("strategy"))
        return None

    # Check numeric
    raw_value = rec.get("value")
    try:
        numeric_value = float(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _logger.warning(
            "tuner_non_numeric_value",
            param=param,
            value=raw_value,
            strategy=rec.get("strategy"),
        )
        return None

    # D-09: clip to bounds
    clipped = clip_value(param, numeric_value, registry)
    if clipped != numeric_value:
        _logger.warning(
            "tuner_value_clipped",
            param=param,
            original=numeric_value,
            clipped=clipped,
            strategy=rec.get("strategy"),
        )

    # D-11: coerce to int if type requires it
    entry = registry[param]
    if entry.value_type == "int":
        return int(round(clipped))

    return clipped


def _group_recommendations(
    recs: list[dict[str, Any]],
    registry: dict[str, BoundsEntry],
) -> dict[str, dict[str, Any]]:
    """Group validated recommendations by strategy name.

    For each rec: validate -> if None skip, else place into strategy bucket.
    If rec["instrument"] is not None, goes into instrument_changes[instrument][param].
    If rec["instrument"] is None, goes into changes[param].

    Args:
        recs: Raw recommendation dicts from Claude's tool output.
        registry: Bounds registry for validation.

    Returns:
        Dict keyed by strategy name:
        {strategy_name: {"changes": {...}, "instrument_changes": {...}, "reasonings": {...}}}
    """
    grouped: dict[str, dict[str, Any]] = {}

    for rec in recs:
        validated = validate_recommendation(rec, registry)
        if validated is None:
            continue

        strategy = rec.get("strategy", "")
        instrument = rec.get("instrument")  # may be None
        param = rec.get("param", "")
        reasoning = rec.get("reasoning", "")

        if strategy not in grouped:
            grouped[strategy] = {"changes": {}, "instrument_changes": {}, "reasonings": {}}

        if instrument is None:
            grouped[strategy]["changes"][param] = validated
        else:
            grouped[strategy]["instrument_changes"].setdefault(instrument, {})[param] = validated

        # Store reasoning keyed by param (last rec for param wins if duplicated)
        grouped[strategy]["reasonings"][param] = reasoning

    return grouped


def run_tuning_cycle(
    fills: list[AttributedFill],
    config_dir: Path,
    bounds_path: Path,
    model: str | None = None,
) -> TuningResult:
    """Orchestrate a complete parameter tuning cycle (D-18).

    Pipeline:
    1. Load bounds registry.
    2. Compute strategy metrics from attributed fills.
    3. Load current strategy params for strategies that have metrics.
    4. Build prompts and call Claude.
    5. On None response (API failure): return empty TuningResult (D-13/D-14).
    6. Validate and group recommendations.
    7. Apply changes per strategy via apply_parameter_changes.
    8. Backfill Claude's reasoning into ParameterChange records.
    9. Log each change via log_parameter_change.
    10. If no recommendations: log_no_change with summary (D-15).
    11. Return TuningResult with summary and all applied changes.

    Args:
        fills: Attributed fill records for metric computation.
        config_dir: Root configs directory (config_dir/strategies/<name>.yaml).
        bounds_path: Path to bounds.yaml.
        model: Override model ID; falls back to TUNER_MODEL env var then DEFAULT_MODEL (D-16).

    Returns:
        TuningResult with Claude's summary and all applied ParameterChange records.
    """
    effective_model = model or os.environ.get("TUNER_MODEL", DEFAULT_MODEL)

    # Step 1: Load bounds registry
    registry = load_bounds_registry(bounds_path)

    # Step 2: Compute metrics
    metrics = compute_strategy_metrics(fills)

    # Step 3: Load current params for strategies with any metric entries
    strategy_names = {source for (source, _instrument) in metrics}
    current_params: dict[str, dict[str, Any]] = {}
    for strategy_name in strategy_names:
        current_params[strategy_name] = load_strategy_config(strategy_name)

    # Step 4: Build prompts
    system_prompt = build_system_prompt()
    user_message = build_user_message(metrics, current_params, registry)

    # Step 5: Call Claude
    response = call_claude(effective_model, system_prompt, user_message)

    # Step 6: Handle API failure (D-13/D-14)
    if response is None:
        return TuningResult(summary="", changes=[])

    summary: str = response.get("summary", "")
    raw_recs: list[dict[str, Any]] = response.get("recommendations", [])

    # Step 7: Validate and group recommendations
    grouped = _group_recommendations(raw_recs, registry)

    # Step 8-10: Apply changes and collect records
    all_changes: list[ParameterChange] = []

    for strategy_name, group in grouped.items():
        strategy_path = config_dir / "strategies" / f"{strategy_name}.yaml"
        changes = group["changes"]
        instrument_changes = group["instrument_changes"]
        reasonings: dict[str, str] = group["reasonings"]

        applied = apply_parameter_changes(strategy_path, changes, instrument_changes, registry)

        # Backfill Claude's reasoning into frozen ParameterChange records
        for change in applied:
            reasoning_text = reasonings.get(change.param, "")
            filled_change = dataclasses.replace(change, reasoning=reasoning_text)
            all_changes.append(filled_change)
            # Step 9: Audit log
            log_parameter_change(filled_change)

    # D-15: log no-change run with Claude's summary
    if not all_changes:
        log_no_change(strategy="all", instrument=None, reasoning=summary)

    return TuningResult(summary=summary, changes=all_changes)
