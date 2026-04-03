"""Async scheduler for LLM-powered strategy orchestration.

Runs a perpetual loop that calls the Claude orchestrator at configurable
intervals (default 4 hours), updates two shared in-memory dicts:

- ``gate_map[(instrument, strategy)] -> bool`` — whether the strategy is
  enabled for that instrument.
- ``param_adjustments[(instrument, strategy)] -> dict[str, float]`` — bounded
  parameter overrides merged into session overrides before each evaluate().

Both dicts are plain dicts (no locks) because asyncio is single-threaded:
the scheduler is the sole writer; the main loop is the sole reader.

The gate map is also mirrored to Redis as a hash
``phantom:orchestrator:gate_map`` with keys ``"instrument:strategy"`` and
values ``"1"`` (enabled) or ``"0"`` (disabled).  This write is
fire-and-forget — a Redis error never crashes the loop.

Design notes:
- All errors are caught per-run; the loop never crashes.
- Structured logging on every decision path: skipped, failed, completed.
- Tick sleep is 60 seconds; actual cadence controlled by update_interval_seconds.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from agents.signals.news_client import fetch_crypto_headlines, fetch_economic_events
from agents.signals.orch_client import (
    OrchestratorParams,
    build_orchestrator_context,
    call_claude_orchestrator,
    validate_orchestrator_response,
)
from libs.common.utils import utc_now

if TYPE_CHECKING:
    from agents.alpha.regime_detector import RegimeDetector
    from agents.signals.feature_store import FeatureStore
    from libs.common.models.market_snapshot import MarketSnapshot

_logger = structlog.get_logger(__name__)

# Seconds between each wake-up tick.  The actual call cadence is controlled by
# OrchestratorParams.update_interval_seconds.
_TICK_SLEEP_SECONDS: int = 60

# Computed once at module load — avoids repeated Path resolution in the hot loop.
_BOUNDS_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "bounds.yaml"
)

# Minimum number of live snapshots before the orchestrator is allowed to run.
# Without this guard the first run fires ~10s after startup with stale regime data.
_MIN_SNAPSHOTS_BEFORE_ORCH: int = 20


async def run_orchestrator_scheduler(
    instrument_ids: list[str],
    slow_stores: dict[str, FeatureStore],
    latest_snapshots: dict[str, MarketSnapshot],
    regime_detector: RegimeDetector,
    redis_client: Any,
    gate_map: dict[tuple[str, str], bool],
    param_adjustments: dict[tuple[str, str], dict[str, Any]],
    params: OrchestratorParams,
) -> None:
    """Perpetual async loop that calls Claude to orchestrate strategy parameters.

    On each tick:
    1. Enforces ``min_interval_seconds`` cooldown — skip if too soon.
    2. Enforces ``update_interval_seconds`` — skip if not yet due.
    3. Assembles multi-instrument context via ``build_orchestrator_context()``.
    4. Calls ``call_claude_orchestrator()``; if None, logs failure and continues.
    5. Validates and clips decisions via ``validate_orchestrator_response()``.
    6. Updates ``gate_map`` and ``param_adjustments`` in-place.
    7. Mirrors gate map to Redis (fire-and-forget).
    8. Logs ``orchestrator_run_completed`` with decision count.

    Args:
        instrument_ids: Instruments to include in context (e.g. ``["ETH-PERP"]``).
        slow_stores: Per-instrument ``FeatureStore`` instances (slow cadence).
        latest_snapshots: Shared mutable dict with most recent ``MarketSnapshot``
            per instrument.  Read-only from this scheduler.
        regime_detector: Shared ``RegimeDetector`` — ``regime_for()`` called per
            instrument inside ``build_orchestrator_context()``.
        redis_client: Async Redis client used to mirror gate map.  Accepts
            ``redis.asyncio.Redis`` at runtime; typed as ``Any`` to avoid
            ``aioredis`` generic type-arg issues with the installed stubs.
        gate_map: Mutable dict updated in-place with enabled/disabled decisions.
            Key: ``(instrument_id, strategy_name)``.  Value: ``bool``.
        param_adjustments: Mutable dict updated in-place with clipped param
            adjustments.  Key: ``(instrument_id, strategy_name)``.
            Value: ``dict[str, float]``.
        params: ``OrchestratorParams`` controlling cadence and model settings.
    """
    last_run_time: float = 0.0  # epoch seconds — 0 means "never run"

    _logger.info(
        "orchestrator_scheduler_started",
        instruments=instrument_ids,
        tick_sleep_seconds=_TICK_SLEEP_SECONDS,
        update_interval_seconds=params.update_interval_seconds,
        min_interval_seconds=params.min_interval_seconds,
    )

    while True:
        try:
            last_run_time = await _run_tick(
                instrument_ids=instrument_ids,
                slow_stores=slow_stores,
                latest_snapshots=latest_snapshots,
                regime_detector=regime_detector,
                redis_client=redis_client,
                gate_map=gate_map,
                param_adjustments=param_adjustments,
                params=params,
                last_run_time=last_run_time,
            )
        except Exception as exc:
            _logger.error(
                "orchestrator_scheduler_tick_error",
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        await asyncio.sleep(_TICK_SLEEP_SECONDS)


async def _run_tick(
    instrument_ids: list[str],
    slow_stores: dict[str, FeatureStore],
    latest_snapshots: dict[str, MarketSnapshot],
    regime_detector: RegimeDetector,
    redis_client: Any,
    gate_map: dict[tuple[str, str], bool],
    param_adjustments: dict[tuple[str, str], dict[str, Any]],
    params: OrchestratorParams,
    last_run_time: float,
) -> float:
    """Execute one scheduler tick — check intervals, call Claude, update dicts.

    Args:
        instrument_ids: Instruments to include in context.
        slow_stores: Per-instrument FeatureStore instances.
        latest_snapshots: Most recent MarketSnapshot per instrument.
        regime_detector: Shared RegimeDetector.
        redis_client: Async Redis client (``Any`` to sidestep stub limitations).
        gate_map: Gate map dict to update in-place.
        param_adjustments: Param adjustments dict to update in-place.
        params: Orchestrator params.
        last_run_time: Epoch seconds of last successful run (0 = never).

    Returns:
        Updated last_run_time (epoch seconds).
    """
    now = utc_now().timestamp()
    elapsed = now - last_run_time

    # Cooldown check — enforced to prevent hammering the API after failures.
    if last_run_time > 0.0 and elapsed < params.min_interval_seconds:
        _logger.debug(
            "orchestrator_run_skipped",
            reason="cooldown",
            elapsed_seconds=round(elapsed, 1),
            min_interval_seconds=params.min_interval_seconds,
        )
        return last_run_time

    # Interval check — skip if not yet due
    if last_run_time > 0.0 and elapsed < params.update_interval_seconds:
        _logger.debug(
            "orchestrator_run_skipped",
            reason="interval_not_elapsed",
            elapsed_seconds=round(elapsed, 1),
            update_interval_seconds=params.update_interval_seconds,
        )
        return last_run_time

    # Warmup guard — wait until the regime detector has seen enough live data
    # for all instruments before calling Claude.  Without this, the first run
    # fires ~10s after startup before any live snapshots have arrived, so
    # regime_for() returns the default RANGING for every instrument and Claude
    # makes bad decisions that persist for the full update_interval_seconds (2h).
    # latest_snapshots is only populated by live snapshot events, so it is a
    # reliable proxy for "the regime detector has real data".
    instruments_ready = [
        iid for iid in instrument_ids if iid in latest_snapshots
    ]
    if len(instruments_ready) < len(instrument_ids):
        _logger.info(
            "orchestrator_run_skipped",
            reason="warmup",
            waiting_for=[i for i in instrument_ids if i not in latest_snapshots],
            min_snapshots=_MIN_SNAPSHOTS_BEFORE_ORCH,
        )
        return last_run_time

    # Also require the regime detector to have processed enough live snapshots
    # per instrument (RegimeDetector needs ≥10 to classify; use 20 for safety).
    insufficient_regime = [
        iid for iid in instrument_ids
        if regime_detector.snapshot_count_for(iid) < _MIN_SNAPSHOTS_BEFORE_ORCH
    ]
    if insufficient_regime:
        _logger.info(
            "orchestrator_run_skipped",
            reason="regime_warmup",
            waiting_for=insufficient_regime,
            min_samples=_MIN_SNAPSHOTS_BEFORE_ORCH,
        )
        return last_run_time

    # Fetch news context concurrently before building the orchestrator context.
    cp_key = os.environ.get("CRYPTOPANIC_API_KEY", "")
    fh_key = os.environ.get("FINNHUB_API_KEY", "")
    headlines, events = await asyncio.gather(
        fetch_crypto_headlines(api_key=cp_key),
        fetch_economic_events(api_key=fh_key),
    )
    _logger.info("news_context_fetched", headline_count=len(headlines), event_count=len(events))

    # Build context and call Claude
    context_str = build_orchestrator_context(
        instrument_ids=instrument_ids,
        slow_stores=slow_stores,
        latest_snapshots=latest_snapshots,
        regime_detector=regime_detector,
        headlines=headlines,
        events=events,
    )

    decisions = await call_claude_orchestrator(context_str, params)
    if decisions is None:
        _logger.warning("orchestrator_run_failed", reason="claude_returned_none")
        # Still update time to avoid hammering the API on repeated failures
        return now

    # Validate and clip against bounds.yaml
    validated = validate_orchestrator_response(decisions, _BOUNDS_PATH)

    # Filter decisions below the confidence threshold
    accepted = [
        d for d in validated if d.get("confidence", 1.0) >= params.min_confidence_threshold
    ]
    skipped_count = len(validated) - len(accepted)
    if skipped_count > 0:
        _logger.debug(
            "orchestrator_decisions_filtered",
            skipped=skipped_count,
            min_confidence_threshold=params.min_confidence_threshold,
        )

    # Update gate map and param adjustments in-place
    for decision in accepted:
        key: tuple[str, str] = (decision["instrument"], decision["strategy"])
        gate_map[key] = decision["enabled"]
        adj: dict[str, Any] = decision.get("param_adjustments") or {}
        if adj:
            param_adjustments[key] = adj
        elif key in param_adjustments:
            # Clear stale adjustments when orchestrator omits them
            del param_adjustments[key]

    # Mirror gate map to Redis (fire-and-forget)
    await _mirror_gate_map_to_redis(gate_map, redis_client)

    _logger.info(
        "orchestrator_run_completed",
        decisions_count=len(accepted),
        instruments=instrument_ids,
    )

    return now


async def _mirror_gate_map_to_redis(
    gate_map: dict[tuple[str, str], bool],
    redis_client: Any,
) -> None:
    """Write the full gate map to Redis as a hash (fire-and-forget).

    Key: ``phantom:orchestrator:gate_map``
    Field: ``"instrument:strategy"``
    Value: ``"1"`` (enabled) or ``"0"`` (disabled)

    Args:
        gate_map: Current gate map dict.
        redis_client: Async Redis client (``Any`` typed; accepts ``redis.asyncio.Redis``).
    """
    if not gate_map:
        return

    mapping: dict[str, str] = {
        f"{inst}:{strat}": "1" if enabled else "0"
        for (inst, strat), enabled in gate_map.items()
    }

    try:
        await redis_client.hset("phantom:orchestrator:gate_map", mapping=mapping)
        _logger.debug(
            "orchestrator_gate_map_mirrored",
            field_count=len(mapping),
        )
    except Exception as exc:
        _logger.warning(
            "orchestrator_gate_map_redis_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
        )
