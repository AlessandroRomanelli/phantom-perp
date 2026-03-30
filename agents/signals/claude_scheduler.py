"""Async scheduler for Claude-powered market analysis signals.

Runs a perpetual loop over configured instruments, decides when to call
Claude based on a periodic cadence *or* market-triggered conditions
(volatility spike, OI shift, regime change), enforces a per-instrument
cooldown, assembles market context, calls Claude, validates the response,
converts it to a ``StandardSignal``, and enqueues it for the
``ClaudeMarketAnalysisStrategy`` bridge to drain on the next evaluate() tick.

Design notes:
- One scheduler task covers all instruments sequentially each wake cycle.
- All errors are caught per-instrument so one bad call cannot halt others.
- Structured logging on every decision path: triggered, skipped, completed,
  failed, no_signal.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from agents.signals.claude_market_client import (
    call_claude_analysis,
)
from libs.common.models.enums import (
    MarketRegime,
    PortfolioTarget,
    PositionSide,
    SignalSource,
)
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, utc_now

if TYPE_CHECKING:
    from agents.alpha.regime_detector import RegimeDetector
    from agents.signals.feature_store import FeatureStore
    from agents.signals.strategies.claude_market_analysis import (
        ClaudeMarketAnalysisParams,
    )

_logger = structlog.get_logger(__name__)

# How long the scheduler sleeps between each full iteration (all instruments).
_TICK_SLEEP_SECONDS: int = 30


async def run_claude_scheduler(
    instrument_ids: list[str],
    slow_stores: dict[str, FeatureStore],
    claude_queues: dict[str, asyncio.Queue[StandardSignal]],
    regime_detector: RegimeDetector,
    settings: object,  # Settings object — only used for logging; params come from strategy configs
    latest_snapshots: dict[str, object],  # MarketSnapshot | None per instrument
    *,
    # Trigger thresholds — configurable for tests; defaults match plan
    vol_spike_threshold: float = 0.20,
    oi_shift_threshold_pct: float = 5.0,
    min_interval_minutes: float = 3.0,
    redis_client: object | None = None,  # Optional redis for persisting analysis state
) -> None:
    """Async scheduler that emits Claude-sourced signals for all instruments.

    Loops forever, waking every ``_TICK_SLEEP_SECONDS`` seconds.  For each
    instrument:

    1. Checks if ``base_interval`` (``analysis_interval_seconds``) has elapsed
       since the last successful call **or** if a trigger condition fires
       (volatility spike, OI shift, regime change).
    2. Enforces ``min_interval_minutes`` cooldown regardless of trigger source.
    3. Calls ``build_market_context()`` and ``call_claude_analysis()``.
    4. Validates response; builds ``StandardSignal`` and enqueues it.

    Args:
        instrument_ids: Instruments to analyse (e.g. ``["ETH-PERP", ...]``).
        slow_stores: Per-instrument ``FeatureStore`` instances (slow cadence).
        claude_queues: Per-instrument signal queues wired to the strategy.
        regime_detector: Shared regime detector (``regime_for()`` called per instrument).
        settings: Application settings (currently used only for startup logging).
        latest_snapshots: Shared mutable dict updated by the main loop with the
            most recent ``MarketSnapshot`` for each instrument.  The scheduler
            reads ``latest_snapshots[instrument_id]`` without modifying the dict.
        vol_spike_threshold: 1h volatility delta that triggers an early call.
        oi_shift_threshold_pct: OI shift (%) that triggers an early call.
        min_interval_minutes: Minimum seconds between calls per instrument.
    """
    # Per-instrument state
    last_call_time: dict[str, float] = {}   # epoch seconds
    prev_oi: dict[str, float] = {}
    prev_regime: dict[str, MarketRegime] = {}
    prev_vol: dict[str, float] = {}

    # Import lazily to avoid circular imports at module load time

    min_interval_seconds = min_interval_minutes * 60.0

    _logger.info(
        "claude_scheduler_started",
        instruments=instrument_ids,
        tick_sleep_seconds=_TICK_SLEEP_SECONDS,
        min_interval_seconds=min_interval_seconds,
        vol_spike_threshold=vol_spike_threshold,
        oi_shift_threshold_pct=oi_shift_threshold_pct,
    )

    while True:
        now = utc_now().timestamp()

        for instrument_id in instrument_ids:
            try:
                await _process_instrument(
                    instrument_id=instrument_id,
                    now=now,
                    slow_stores=slow_stores,
                    claude_queues=claude_queues,
                    regime_detector=regime_detector,
                    last_call_time=last_call_time,
                    prev_oi=prev_oi,
                    prev_regime=prev_regime,
                    prev_vol=prev_vol,
                    min_interval_seconds=min_interval_seconds,
                    vol_spike_threshold=vol_spike_threshold,
                    oi_shift_threshold_pct=oi_shift_threshold_pct,
                    load_params_fn=_load_params,
                    latest_snapshots=latest_snapshots,
                    redis_client=redis_client,
                )
            except Exception as exc:
                _logger.error(
                    "claude_scheduler_instrument_error",
                    instrument=instrument_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )

        await asyncio.sleep(_TICK_SLEEP_SECONDS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _process_instrument(
    instrument_id: str,
    now: float,
    slow_stores: dict[str, FeatureStore],
    claude_queues: dict[str, asyncio.Queue[StandardSignal]],
    regime_detector: RegimeDetector,
    last_call_time: dict[str, float],
    prev_oi: dict[str, float],
    prev_regime: dict[str, MarketRegime],
    prev_vol: dict[str, float],
    min_interval_seconds: float,
    vol_spike_threshold: float,
    oi_shift_threshold_pct: float,
    load_params_fn: object,
    latest_snapshots: dict[str, object],
    redis_client: object | None = None,
) -> None:
    """Evaluate triggers and call Claude for a single instrument."""
    store = slow_stores.get(instrument_id)
    queue = claude_queues.get(instrument_id)

    if store is None or queue is None:
        _logger.debug(
            "claude_scheduler_no_store_or_queue",
            instrument=instrument_id,
        )
        return

    # Need at least one snapshot in the store to have a meaningful context
    if store.sample_count == 0:
        _logger.debug(
            "claude_scheduler_empty_store",
            instrument=instrument_id,
        )
        return

    # Get latest snapshot from the shared dict updated by the main loop
    snapshot = latest_snapshots.get(instrument_id)  # type: ignore[assignment]
    if snapshot is None:
        _logger.debug(
            "claude_scheduler_no_snapshot",
            instrument=instrument_id,
        )
        return

    # Load per-instrument params from YAML (cheap dict lookup, no I/O hot path)
    params = load_params_fn(instrument_id)  # type: ignore[operator]

    base_interval = float(params.analysis_interval_seconds)
    min_conviction = params.min_conviction

    # Cooldown check — always enforced regardless of trigger
    last_t = last_call_time.get(instrument_id, 0.0)
    elapsed = now - last_t
    if elapsed < min_interval_seconds:
        _logger.debug(
            "claude_scheduler_cooldown",
            instrument=instrument_id,
            elapsed_seconds=round(elapsed, 1),
            cooldown_seconds=min_interval_seconds,
        )
        return

    # Detect current market state from store and regime detector
    current_regime = regime_detector.regime_for(instrument_id)
    open_interests = store.open_interests

    current_vol = float(snapshot.volatility_1h)
    current_oi = float(open_interests[-1]) if len(open_interests) > 0 else 0.0

    # --- Trigger evaluation ---
    base_elapsed = elapsed >= base_interval
    trigger_reason: str | None = None

    if base_elapsed:
        trigger_reason = "base_interval"
    else:
        # Volatility spike trigger
        p_vol = prev_vol.get(instrument_id, current_vol)
        if p_vol > 0 and abs(current_vol - p_vol) / p_vol > vol_spike_threshold:
            trigger_reason = "vol_spike"

        # OI shift trigger
        if trigger_reason is None:
            p_oi = prev_oi.get(instrument_id, current_oi)
            if p_oi > 0:
                oi_shift = abs(current_oi - p_oi) / p_oi * 100.0
                if oi_shift > oi_shift_threshold_pct:
                    trigger_reason = "oi_shift"

        # Regime change trigger
        if trigger_reason is None:
            p_regime = prev_regime.get(instrument_id)
            if p_regime is not None and p_regime != current_regime:
                trigger_reason = "regime_change"

    if trigger_reason is None:
        # No trigger condition met and base interval not elapsed
        _logger.debug(
            "claude_scheduler_skipped",
            instrument=instrument_id,
            elapsed_seconds=round(elapsed, 1),
            base_interval_seconds=base_interval,
        )
        return

    _logger.info(
        "claude_scheduler_triggered",
        instrument=instrument_id,
        trigger=trigger_reason,
        elapsed_seconds=round(elapsed, 1),
        regime=current_regime.value,
        volatility_1h=current_vol,
    )

    # Update previous state tracking (always, even if call fails)
    prev_vol[instrument_id] = current_vol
    prev_oi[instrument_id] = current_oi
    prev_regime[instrument_id] = current_regime

    # Call Claude
    try:
        validated = await call_claude_analysis(
            instrument_id=instrument_id,
            store=store,
            snapshot=snapshot,
            regime=current_regime,
        )
    except Exception as exc:
        _logger.error(
            "claude_scheduler_call_failed",
            instrument=instrument_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        last_call_time[instrument_id] = now
        return

    last_call_time[instrument_id] = now

    if validated is None or validated.get("direction") == "NO_SIGNAL":
        _logger.info(
            "claude_scheduler_no_signal",
            instrument=instrument_id,
            trigger=trigger_reason,
        )
        # Persist NO_SIGNAL state to Redis — include Claude's actual reasoning
        _persist_claude_state(
            redis_client=redis_client,
            instrument_id=instrument_id,
            state={
                "direction": "NO_SIGNAL",
                "conviction": 0.0,
                "reasoning": validated.get("reasoning", "No clear trade opportunity.") if validated else "No clear trade opportunity.",
                "regime": current_regime.value,
                "trigger": trigger_reason,
                "timestamp": utc_now().isoformat(),
                "volatility_1h": round(current_vol, 4),
            },
        )
        return
    # Filter by conviction threshold
    conviction = float(validated["conviction"])
    if conviction < min_conviction:
        _logger.info(
            "claude_scheduler_conviction_too_low",
            instrument=instrument_id,
            conviction=round(conviction, 3),
            threshold=min_conviction,
        )
        # Persist low-conviction state to Redis
        _persist_claude_state(
            redis_client=redis_client,
            instrument_id=instrument_id,
            state={
                "direction": str(validated.get("direction", "?")),
                "conviction": round(conviction, 3),
                "reasoning": str(validated.get("reasoning", "")),
                "regime": current_regime.value,
                "trigger": trigger_reason,
                "timestamp": utc_now().isoformat(),
                "volatility_1h": round(current_vol, 4),
                "below_threshold": True,
            },
        )
        return

    # Build StandardSignal
    direction: PositionSide = validated["direction"]
    time_horizon_hours: float = validated.get("time_horizon_hours", params.default_time_horizon_hours)

    # Portfolio routing: high conviction → A, otherwise None (router decides)
    suggested_target: PortfolioTarget | None = None
    if conviction >= params.portfolio_a_min_conviction:
        suggested_target = PortfolioTarget.A

    signal = StandardSignal(
        signal_id=generate_id("claude"),
        timestamp=utc_now(),
        instrument=instrument_id,
        direction=direction,
        conviction=conviction,
        source=SignalSource.CLAUDE_MARKET_ANALYSIS,
        time_horizon=timedelta(hours=time_horizon_hours),
        reasoning=str(validated.get("reasoning", "")),
        suggested_target=suggested_target,
        entry_price=validated.get("entry_price"),
        stop_loss=validated.get("stop_loss"),
        take_profit=validated.get("take_profit"),
        metadata={
            "trigger": trigger_reason,
            "regime": current_regime.value,
        },
    )

    # Enqueue — drop if queue is full (non-blocking put_nowait)
    try:
        queue.put_nowait(signal)
        _logger.info(
            "claude_scheduler_signal_enqueued",
            instrument=instrument_id,
            signal_id=signal.signal_id,
            direction=direction.value,
            conviction=round(conviction, 3),
            trigger=trigger_reason,
            queue_size=queue.qsize(),
        )
    except asyncio.QueueFull:
        _logger.warning(
            "claude_scheduler_queue_full",
            instrument=instrument_id,
            signal_id=signal.signal_id,
            queue_maxsize=queue.maxsize,
        )

    # Persist signal state to Redis for dashboard visibility (fire-and-forget)
    _persist_claude_state(
        redis_client=redis_client,
        instrument_id=instrument_id,
        state={
            "direction": direction.value,
            "conviction": round(conviction, 3),
            "reasoning": str(validated.get("reasoning", "")),
            "regime": current_regime.value,
            "trigger": trigger_reason,
            "timestamp": utc_now().isoformat(),
            "volatility_1h": round(current_vol, 4),
            "entry_price": str(validated["entry_price"]) if validated.get("entry_price") else None,
            "stop_loss": str(validated["stop_loss"]) if validated.get("stop_loss") else None,
            "take_profit": str(validated["take_profit"]) if validated.get("take_profit") else None,
            "signal_emitted": True,
        },
    )


def _persist_claude_state(
    redis_client: object | None,
    instrument_id: str,
    state: dict,
) -> None:
    """Fire-and-forget Redis write of last Claude analysis per instrument.

    Uses asyncio.ensure_future so we never block the scheduler tick on I/O.
    Silently ignores all errors — Redis is an optional observability mirror.

    Args:
        redis_client: Optional async Redis client (None = skip).
        instrument_id: Instrument key (e.g. 'ETH-PERP').
        state: Dict of analysis state to serialise and store.
    """
    if redis_client is None:
        return
    import orjson  # noqa: PLC0415

    async def _write() -> None:
        try:
            await redis_client.hset(  # type: ignore[union-attr]
                "phantom:claude:last_analysis",
                instrument_id,
                orjson.dumps(state).decode(),
            )
        except Exception as exc:
            _logger.warning(
                "claude_scheduler_redis_persist_failed",
                instrument=instrument_id,
                error=str(exc),
            )

    asyncio.ensure_future(_write())


def _load_params(instrument_id: str) -> ClaudeMarketAnalysisParams:
    """Load ClaudeMarketAnalysisParams for an instrument from YAML config.

    Imports lazily to avoid circular import at module level.

    Args:
        instrument_id: Instrument ID to load params for.

    Returns:
        Resolved ``ClaudeMarketAnalysisParams`` with per-instrument overrides.
    """
    from agents.signals.strategies.claude_market_analysis import (  # noqa: PLC0415
        ClaudeMarketAnalysisParams,
    )
    from libs.common.config import load_strategy_config_for_instrument  # noqa: PLC0415

    config = load_strategy_config_for_instrument("claude_market_analysis", instrument_id)
    p = config.get("parameters", {})
    return ClaudeMarketAnalysisParams(
        enabled=p.get("enabled", True),
        weight=p.get("weight", 0.15),
        analysis_interval_seconds=p.get("analysis_interval_seconds", 300),
        max_queue_size=p.get("max_queue_size", 50),
        min_conviction=p.get("min_conviction", 0.50),
        portfolio_a_min_conviction=p.get("portfolio_a_min_conviction", 0.75),
        default_time_horizon_hours=p.get("default_time_horizon_hours", 4.0),
    )
