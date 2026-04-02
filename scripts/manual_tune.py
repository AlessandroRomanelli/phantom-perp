"""One-shot manual tuning script.

Context: The alpha combiner agreement filter (min_agreeing_sources=2) is now
active with a 300-second combination window. Signals are flowing but the
route_a_min_conviction thresholds (0.85 across all active strategies) are
too high for signals to clear risk after combining. We want Claude to
recommend lower route_a_min_conviction and min_conviction values to allow
more combined signals through, without removing quality gates entirely.

Usage (from project root, venv active):
    python scripts/manual_tune.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from libs.common.config import load_strategy_config
from libs.tuner.audit import log_no_change, log_parameter_change
from libs.tuner.bounds import load_bounds_registry
from libs.tuner.claude_client import call_claude, DEFAULT_MODEL, TOOL_SCHEMA
from libs.tuner.recommender import _group_recommendations
from libs.tuner.writer import apply_parameter_changes
import dataclasses
import anthropic
import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
)
logger = structlog.get_logger("manual_tune")

CONFIG_DIR = Path(__file__).parent.parent / "configs"
BOUNDS_PATH = CONFIG_DIR / "bounds.yaml"
STRATEGIES_DIR = CONFIG_DIR / "strategies"

# Strategies currently generating real signals — the active set
ACTIVE_STRATEGIES = [
    "orderbook_imbalance",
    "correlation",
    "momentum",
    "funding_arb",
    "oi_divergence",
    "regime_trend",
    "mean_reversion",
]


SYSTEM_PROMPT = """\
You are a trading strategy parameter tuner for Portfolio A of a perpetual futures
trading bot on Coinbase International Exchange.

Your task is to recommend parameter adjustments that will allow more signals through
the pipeline while maintaining quality gates. Do NOT remove safety thresholds —
lower them modestly and proportionally.

Rules:
1. Only adjust parameters that appear in the bounds registry. Do not invent new parameters.
2. All recommended values MUST respect the min/max bounds listed in the registry.
3. Provide clear reasoning for each recommendation.
4. Use the submit_recommendations tool to return your analysis.
"""


def build_context_message(
    current_params: dict[str, dict],
    registry: dict,
) -> str:
    """Build a focused context message explaining the tuning goal."""
    lines = [
        "## Context",
        "",
        "The alpha combiner was previously set to min_agreeing_sources=1 (any single signal",
        "triggers a trade idea). It has just been changed to min_agreeing_sources=2 with a",
        "300-second combination window. This means two independent strategies must agree on",
        "direction for the same instrument within 5 minutes before a trade idea is emitted.",
        "",
        "Result: far fewer trade ideas are reaching the risk agent. The current",
        "route_a_min_conviction thresholds (0.85 on most strategies) were calibrated for a",
        "world where single signals could trade autonomously on Route A. Now that signals must",
        "agree before firing, the effective bar is already higher — so individual strategy",
        "route_a_min_conviction values can be relaxed to allow combined signals through.",
        "",
        "Goal: lower route_a_min_conviction across active strategies to ~0.65-0.75.",
        "Also consider lowering base min_conviction on strategies that are overly conservative.",
        "Do NOT touch stop_loss_atr_mult, take_profit_atr_mult, cooldown_bars, or weight.",
        "Focus only on conviction thresholds.",
        "",
        "Active instruments: ETH-PERP, BTC-PERP, SOL-PERP.",
        "Active strategies (in order of recent signal frequency): orderbook_imbalance,",
        "correlation, momentum, funding_arb, oi_divergence, regime_trend, mean_reversion.",
        "",
        "## Current Parameter Values",
        "",
    ]

    for strategy_name, config in sorted(current_params.items()):
        lines.append(f"strategy: {strategy_name}")
        base_params = config.get("parameters", {})
        for k, v in sorted(base_params.items()):
            if "conviction" in k:
                lines.append(f"  parameters.{k}: {v}")
        instruments = config.get("instruments", {})
        for inst_id, inst_cfg in sorted(instruments.items()):
            inst_params = inst_cfg.get("parameters", inst_cfg)
            for k, v in sorted(inst_params.items()):
                if "conviction" in k:
                    lines.append(f"  instruments.{inst_id}.parameters.{k}: {v}")
        lines.append("")

    lines += [
        "## Bounds Registry (hard limits)",
        "| param | min | max | type |",
        "|-------|-----|-----|------|",
    ]
    for param_name, entry in sorted(registry.items()):
        if "conviction" in param_name:
            min_s = f"{entry.min_value:.2f}" if entry.value_type == "float" else str(int(entry.min_value))
            max_s = f"{entry.max_value:.2f}" if entry.value_type == "float" else str(int(entry.max_value))
            lines.append(f"| {param_name} | {min_s} | {max_s} | {entry.value_type} |")

    lines += [
        "",
        "## Tunable Parameters (conviction only for this run)",
        "- **min_conviction**: Minimum conviction to emit a signal from the strategy",
        "- **route_a_min_conviction**: Minimum conviction for Route A autonomous execution",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual conviction threshold tuning")
    parser.add_argument("--dry-run", action="store_true", help="Print recommendations without applying")
    args = parser.parse_args()

    registry = load_bounds_registry(BOUNDS_PATH)
    current_params = {s: load_strategy_config(s) for s in ACTIVE_STRATEGIES}

    user_message = build_context_message(current_params, registry)

    logger.info("manual_tune_calling_claude", strategies=ACTIVE_STRATEGIES, dry_run=args.dry_run)

    response = call_claude(DEFAULT_MODEL, SYSTEM_PROMPT, user_message)

    if response is None:
        logger.error("manual_tune_claude_failed")
        sys.exit(1)

    summary = response.get("summary", "")
    raw_recs = response.get("recommendations", [])

    logger.info("manual_tune_response_received",
                summary=summary[:200],
                recommendation_count=len(raw_recs))

    if not raw_recs:
        logger.info("manual_tune_no_changes", summary=summary)
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Claude summary: {summary}")
    print(f"{'='*60}")
    for rec in raw_recs:
        inst = rec.get("instrument") or "base"
        print(f"  {rec['strategy']}/{inst}: {rec['param']} = {rec['value']}  ({rec['reasoning'][:80]}...)")
    print(f"{'='*60}\n")

    if args.dry_run:
        logger.info("manual_tune_dry_run_complete", changes=len(raw_recs))
        sys.exit(0)

    grouped = _group_recommendations(raw_recs, registry)
    all_changes = []

    for strategy_name, group in grouped.items():
        strategy_path = STRATEGIES_DIR / f"{strategy_name}.yaml"
        if not strategy_path.exists():
            logger.warning("manual_tune_strategy_not_found", strategy=strategy_name)
            continue
        applied = apply_parameter_changes(
            strategy_path, group["changes"], group["instrument_changes"], registry
        )
        reasonings = group["reasonings"]
        for change in applied:
            filled = dataclasses.replace(change, reasoning=reasonings.get(change.param, ""))
            all_changes.append(filled)
            log_parameter_change(filled)

    if not all_changes:
        log_no_change(strategy="all", instrument=None, reasoning=summary)
        logger.info("manual_tune_no_changes_applied")
    else:
        logger.info("manual_tune_complete", changes_applied=len(all_changes))
        for c in all_changes:
            inst = c.instrument or "base"
            print(f"  Applied: {c.strategy}/{inst}: {c.param}  {c.old_value} → {c.new_value}")


if __name__ == "__main__":
    main()
