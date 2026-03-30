"""Signal generation agent entrypoint.

Consumes MarketSnapshots from stream:market_snapshots, routes them to
per-instrument FeatureStores, runs per-instrument strategy instances
(each with their own parameter overrides), and publishes resulting
StandardSignals to stream:signals.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import redis.asyncio as aioredis  # noqa: TC002
import yaml
from sqlalchemy.exc import SQLAlchemyError

from agents.alpha.regime_detector import RegimeDetector
from agents.signals.claude_scheduler import run_claude_scheduler
from agents.signals.coinglass_poller import run_coinglass_poller
from agents.signals.conviction_normalizer import normalize_conviction, should_route_portfolio_a
from agents.signals.feature_store import FeatureStore
from agents.signals.orch_client import OrchestratorParams
from agents.signals.orch_scheduler import run_orchestrator_scheduler
from agents.signals.session_classifier import SessionType, classify_session
from agents.signals.strategies.base import SignalStrategy  # noqa: TC001
from agents.signals.strategies.claude_market_analysis import (
    ClaudeMarketAnalysisParams,
    ClaudeMarketAnalysisStrategy,
)
from agents.signals.strategies.contrarian_funding import (
    ContrarianFundingParams,
    ContrarianFundingStrategy,
)
from agents.signals.strategies.correlation import CorrelationParams, CorrelationStrategy
from agents.signals.strategies.liquidation_cascade import (
    LiquidationCascadeParams,
    LiquidationCascadeStrategy,
)
from agents.signals.strategies.mean_reversion import MeanReversionParams, MeanReversionStrategy
from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy
from agents.signals.strategies.oi_divergence import OIDivergenceParams, OIDivergenceStrategy
from agents.signals.strategies.orderbook_imbalance import (
    OrderbookImbalanceParams,
    OrderbookImbalanceStrategy,
)
from agents.signals.strategies.regime_trend import RegimeTrendParams, RegimeTrendStrategy
from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy
from libs.common.config import (
    get_settings,
    load_strategy_config,
    load_strategy_config_for_instrument,
    log_config_diff,
    validate_strategy_config,
)
from libs.common.instruments import get_active_instrument_ids
from libs.common.logging import setup_logging
from libs.common.models.enums import PortfolioTarget
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal  # noqa: TC001
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from libs.storage.models import SignalRecord
from libs.storage.relational import RelationalStore, init_db
from libs.storage.repository import TunerRepository

logger = setup_logging("signals", json_output=False)

# -- FeatureStore checkpoint persistence --
_CHECKPOINT_KEY_PREFIX = "phantom:feature_store_checkpoint"
_CHECKPOINT_TTL_SECONDS = 172_800  # 48 hours


def _checkpoint_key(instrument: str, speed: str) -> str:
    """Build Redis key for a feature store checkpoint."""
    return f"{_CHECKPOINT_KEY_PREFIX}:{instrument}:{speed}"


async def _restore_store(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    instrument: str,
    speed: str,
    max_samples: int,
    sample_interval: timedelta,
) -> FeatureStore:
    """Restore a FeatureStore from Redis checkpoint, or create an empty one."""
    key = _checkpoint_key(instrument, speed)
    try:
        raw: bytes | None = await redis.get(key)
        if raw is not None:
            data: dict[str, Any] = orjson.loads(raw)
            store = FeatureStore.from_checkpoint(
                data,
                max_samples=max_samples,
                sample_interval=sample_interval,
            )
            logger.info(
                "feature_store_restored",
                instrument=instrument,
                speed=speed,
                sample_count=store.sample_count,
            )
            return store
    except Exception as exc:
        logger.warning(
            "feature_store_restore_failed",
            instrument=instrument,
            speed=speed,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
    return FeatureStore(max_samples=max_samples, sample_interval=sample_interval)


async def _checkpoint_store(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    store: FeatureStore,
    instrument: str,
    speed: str,
) -> None:
    """Persist a FeatureStore checkpoint to Redis with TTL."""
    key = _checkpoint_key(instrument, speed)
    payload = orjson.dumps(store.to_checkpoint())
    await redis.set(key, payload, ex=_CHECKPOINT_TTL_SECONDS)


# Strategy name -> class mapping.  Add new strategies here.
STRATEGY_CLASSES: dict[str, type[SignalStrategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "liquidation_cascade": LiquidationCascadeStrategy,
    "correlation": CorrelationStrategy,
    "regime_trend": RegimeTrendStrategy,
    "orderbook_imbalance": OrderbookImbalanceStrategy,
    "vwap": VWAPStrategy,
    "funding_arb": ContrarianFundingStrategy,
    "claude_market_analysis": ClaudeMarketAnalysisStrategy,
    "oi_divergence": OIDivergenceStrategy,
}

STRATEGY_PARAMS_CLASSES: dict[str, type] = {
    "momentum": MomentumParams,
    "mean_reversion": MeanReversionParams,
    "liquidation_cascade": LiquidationCascadeParams,
    "correlation": CorrelationParams,
    "regime_trend": RegimeTrendParams,
    "orderbook_imbalance": OrderbookImbalanceParams,
    "vwap": VWAPParams,
    "funding_arb": ContrarianFundingParams,
    "claude_market_analysis": ClaudeMarketAnalysisParams,
    "oi_divergence": OIDivergenceParams,
}


def load_strategy_matrix() -> dict[str, Any]:
    """Load the strategy-instrument enablement matrix."""
    matrix_path = Path(__file__).resolve().parent.parent.parent / "configs" / "strategy_matrix.yaml"
    if not matrix_path.exists():
        return {}
    with open(matrix_path) as f:
        return yaml.safe_load(f) or {}


def load_session_config() -> dict[str, Any]:
    """Load session-aware parameter overrides from configs/sessions.yaml.

    Returns:
        Parsed YAML dict with 'instrument_types' and 'strategies' keys,
        or empty dict if file not found.
    """
    session_path = Path(__file__).resolve().parent.parent.parent / "configs" / "sessions.yaml"
    if not session_path.exists():
        return {}
    with open(session_path) as f:
        return yaml.safe_load(f) or {}


def get_session_overrides(
    session_config: dict[str, Any],
    strategy_name: str,
    session_type: SessionType,
    instrument: str,
) -> dict[str, Any]:
    """Look up session overrides for a strategy and instrument.

    For equity instruments during non-equity-hours weekday sessions,
    also checks the equity_off_hours key.

    Args:
        session_config: Parsed session YAML config.
        strategy_name: Strategy name (e.g. 'momentum').
        session_type: Current session type classification.
        instrument: Instrument ID (e.g. 'ETH-PERP').

    Returns:
        Dict of parameter overrides, or empty dict if none apply.
    """
    strategies = session_config.get("strategies", {})
    strategy_overrides = strategies.get(strategy_name, {})

    # Determine if this is an equity instrument
    instrument_types = session_config.get("instrument_types", {})
    equity_instruments = instrument_types.get("equity", [])
    is_equity = instrument in equity_instruments

    # For equity instruments during crypto_weekday (non-equity hours),
    # use equity_off_hours overrides
    if is_equity and session_type == SessionType.CRYPTO_WEEKDAY:
        return dict(strategy_overrides.get("equity_off_hours", {}))

    # Standard lookup by session type value
    return dict(strategy_overrides.get(session_type.value, {}))


def _apply_session_overrides(
    strategy: SignalStrategy,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Temporarily apply session overrides to strategy params.

    Saves original values before overwriting so they can be restored.

    Args:
        strategy: Strategy instance to mutate.
        overrides: Dict of param_name -> override_value.

    Returns:
        Dict of param_name -> original_value for restoration.
    """
    if not overrides:
        return {}

    originals: dict[str, Any] = {}
    params = strategy._params  # type: ignore[attr-defined]
    for key, value in overrides.items():
        if hasattr(params, key):
            originals[key] = getattr(params, key)
            object.__setattr__(params, key, value)
    return originals


def _restore_params(
    strategy: SignalStrategy,
    originals: dict[str, Any],
) -> None:
    """Restore original parameter values after session override application.

    Args:
        strategy: Strategy instance to restore.
        originals: Dict of param_name -> original_value.
    """
    if not originals:
        return

    params = strategy._params  # type: ignore[attr-defined]
    for key, value in originals.items():
        object.__setattr__(params, key, value)


def _apply_conviction_normalization(signal: StandardSignal) -> StandardSignal:
    """Apply conviction normalization and unified Portfolio A routing.

    Post-processes a signal by:
    1. Computing conviction band via normalize_conviction.
    2. If conviction meets unified threshold, setting suggested_target to A.

    Args:
        signal: Original signal from strategy.

    Returns:
        Updated signal with conviction_band metadata and possibly updated target.
    """
    result = normalize_conviction(signal.conviction)
    updated_metadata = {**signal.metadata, "conviction_band": result.band}

    if should_route_portfolio_a(signal.conviction):
        return replace(
            signal,
            suggested_target=PortfolioTarget.A,
            metadata=updated_metadata,
        )

    return replace(signal, metadata=updated_metadata)


def deserialize_snapshot(payload: dict[str, Any]) -> MarketSnapshot:
    """Rebuild a MarketSnapshot from a Redis stream payload dict."""
    return MarketSnapshot(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        mark_price=Decimal(payload["mark_price"]),
        index_price=Decimal(payload["index_price"]),
        last_price=Decimal(payload["last_price"]),
        best_bid=Decimal(payload["best_bid"]),
        best_ask=Decimal(payload["best_ask"]),
        spread_bps=float(payload["spread_bps"]),
        volume_24h=Decimal(payload["volume_24h"]),
        open_interest=Decimal(payload["open_interest"]),
        funding_rate=Decimal(payload["funding_rate"]),
        next_funding_time=datetime.fromisoformat(payload["next_funding_time"]),
        hours_since_last_funding=float(payload["hours_since_last_funding"]),
        orderbook_imbalance=float(payload["orderbook_imbalance"]),
        volatility_1h=float(payload["volatility_1h"] or 0.0),
        volatility_24h=float(payload["volatility_24h"] or 0.0),
    )


def _json_safe(value: Any) -> Any:
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(value, (np.floating, np.float64, np.float32)):
        return float(value)
    if isinstance(value, (np.integer, np.int64, np.int32)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def signal_to_dict(signal: StandardSignal) -> dict[str, Any]:
    """Serialize a StandardSignal to a JSON-compatible dict for Redis."""
    return {
        "signal_id": signal.signal_id,
        "timestamp": signal.timestamp.isoformat(),
        "instrument": signal.instrument,
        "direction": signal.direction.value,
        "conviction": float(signal.conviction),
        "source": signal.source.value,
        "time_horizon_seconds": int(signal.time_horizon.total_seconds()),
        "reasoning": signal.reasoning,
        "suggested_target": signal.suggested_target.value if signal.suggested_target else None,
        "entry_price": str(signal.entry_price) if signal.entry_price else None,
        "stop_loss": str(signal.stop_loss) if signal.stop_loss else None,
        "take_profit": str(signal.take_profit) if signal.take_profit else None,
        "metadata": _json_safe(signal.metadata),
    }


def build_strategies_for_instrument(instrument_id: str) -> list[SignalStrategy]:
    """Instantiate all enabled strategies for a specific instrument.

    Each strategy gets its own config with per-instrument parameter
    overrides merged in.  A strategy can be disabled for a specific
    instrument by setting ``enabled: false`` in the instrument override
    section of the strategy YAML.

    Args:
        instrument_id: Instrument to build strategies for.

    Returns:
        List of enabled SignalStrategy instances for this instrument.
    """
    strategies: list[SignalStrategy] = []
    matrix = load_strategy_matrix()

    for strategy_name, strategy_cls in STRATEGY_CLASSES.items():
        # Matrix global toggle overrides per-strategy YAML
        matrix_entry = matrix.get("strategies", {}).get(strategy_name, {})
        if not matrix_entry.get("enabled", True):
            continue

        # Matrix instrument list controls per-instrument enablement
        matrix_instruments = matrix_entry.get("instruments", [])
        if matrix_instruments and instrument_id not in matrix_instruments:
            continue

        # Validate raw config schema before merging instrument overrides
        raw_config = load_strategy_config(strategy_name)
        params_cls = STRATEGY_PARAMS_CLASSES.get(strategy_name)
        if raw_config and params_cls:
            validate_strategy_config(strategy_name, raw_config, params_cls)

        config = load_strategy_config_for_instrument(strategy_name, instrument_id)

        # Log which parameters differ from defaults for this instrument
        if raw_config and params_cls:
            default_params = raw_config.get("parameters", {})
            merged_params = config.get("parameters", {})
            log_config_diff(strategy_name, instrument_id, merged_params, default_params)

        # Check if strategy is disabled globally or for this instrument
        strategy_meta = config.get("strategy", {})
        if not strategy_meta.get("enabled", True):
            continue

        strategies.append(strategy_cls(config=config))

    return [s for s in strategies if s.enabled]


async def run_agent() -> None:
    """Main event loop for the signal generation agent."""
    settings = get_settings()

    # Initialize PostgreSQL storage for signal metadata persistence
    db_store = RelationalStore(settings.infra.database_url)
    await init_db(db_store.engine)
    repo = TunerRepository(db_store)
    logger.info("signal_db_initialized")

    # Determine active instruments
    yaml_instruments = (
        settings.yaml_config.get("instruments", {}).get("active")
    )
    instrument_ids = yaml_instruments if yaml_instruments else get_active_instrument_ids()

    # Load session config once at startup
    session_config = load_session_config()
    if session_config:
        logger.info(
            "session_config_loaded",
            strategies=list(session_config.get("strategies", {}).keys()),
        )

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    # Per-instrument feature stores: slow (5 min) for trend/reversion strategies,
    # fast (30s) for high-frequency strategies like orderbook_imbalance.
    _SLOW_INTERVAL = timedelta(seconds=300)  # noqa: N806  # 5 min bars
    _FAST_INTERVAL = timedelta(seconds=30)   # noqa: N806  # 30s bars
    _FAST_STRATEGIES = frozenset({"orderbook_imbalance", "liquidation_cascade"})  # noqa: N806

    slow_stores: dict[str, FeatureStore] = {}
    fast_stores: dict[str, FeatureStore] = {}
    for iid in instrument_ids:
        slow_stores[iid] = await _restore_store(
            publisher._redis, iid, "slow", 500, _SLOW_INTERVAL,
        )
        fast_stores[iid] = await _restore_store(
            publisher._redis, iid, "fast", 500, _FAST_INTERVAL,
        )

    # Per-instrument strategy instances (each with merged params)
    strategies_by_instrument: dict[str, list[SignalStrategy]] = {
        iid: build_strategies_for_instrument(iid)
        for iid in instrument_ids
    }

    for iid, strats in strategies_by_instrument.items():
        logger.info(
            "instrument_strategies",
            instrument=iid,
            strategies=[s.name for s in strats],
        )

    logger.info(
        "feature_store_config",
        slow_interval_sec=_SLOW_INTERVAL.total_seconds(),
        fast_interval_sec=_FAST_INTERVAL.total_seconds(),
        fast_strategies=sorted(_FAST_STRATEGIES),
    )

    # --- Claude Market Analysis integration ---
    # Shared mutable dicts updated in the main loop; scheduler reads from them.
    latest_snapshots: dict[str, MarketSnapshot] = {}
    regime_detector = RegimeDetector()

    # Create one signal queue per instrument for Claude → strategy bridging.
    # Queue maxsize is per-instrument max_queue_size from strategy config.
    claude_queues: dict[str, asyncio.Queue[StandardSignal]] = {}
    for iid in instrument_ids:
        from agents.signals.claude_scheduler import _load_params  # noqa: PLC0415
        p = _load_params(iid)
        claude_queues[iid] = asyncio.Queue(maxsize=p.max_queue_size)

    # Wire each ClaudeMarketAnalysisStrategy instance to its queue.
    for iid, strats in strategies_by_instrument.items():
        q = claude_queues.get(iid)
        if q is None:
            continue
        for s in strats:
            if isinstance(s, ClaudeMarketAnalysisStrategy):
                s.set_queue(q)
                logger.info(
                    "claude_strategy_wired",
                    instrument=iid,
                    queue_maxsize=q.maxsize,
                )

    # Launch the Claude scheduler as a background task.
    asyncio.create_task(
        run_claude_scheduler(
            instrument_ids=instrument_ids,
            slow_stores=slow_stores,
            claude_queues=claude_queues,
            regime_detector=regime_detector,
            settings=settings,
            latest_snapshots=latest_snapshots,
            redis_client=publisher._redis,
        ),
        name="claude_scheduler",
    )
    logger.info("claude_scheduler_task_launched", instruments=instrument_ids)

    # --- Coinglass heatmap poller integration ---
    # Shared mutable dict updated by poller; LiquidationCascadeStrategy reads from it.
    import os  # noqa: PLC0415
    latest_heatmaps: dict[str, list] = {}
    coinglass_api_key = os.environ.get("COINGLASS_API_KEY", "")
    if coinglass_api_key:
        asyncio.create_task(
            run_coinglass_poller(
                instrument_ids=instrument_ids,
                latest_heatmaps=latest_heatmaps,
                latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                api_key=coinglass_api_key,
            ),
            name="coinglass_poller",
        )
        logger.info("coinglass_poller_task_launched", instruments=instrument_ids)
    else:
        logger.info("coinglass_poller_skipped", reason="COINGLASS_API_KEY not set")

    # Wire heatmap store into each LiquidationCascadeStrategy instance.
    for iid, strats in strategies_by_instrument.items():
        for s in strats:
            if isinstance(s, LiquidationCascadeStrategy):
                s.set_heatmap_store(latest_heatmaps)
                logger.info(
                    "liquidation_cascade_heatmap_wired",
                    instrument=iid,
                )

    # --- LLM Strategy Orchestrator ---
    # Shared mutable dicts — no locks needed (asyncio single-threaded; scheduler
    # is sole writer, main loop is sole reader).
    orchestrator_gate_map: dict[tuple[str, str], bool] = {}
    orchestrator_param_adj: dict[tuple[str, str], dict[str, Any]] = {}

    orch_config = load_strategy_config("orchestrator")
    orch_params = OrchestratorParams(**(orch_config or {}).get("parameters", {}))

    if orch_params.enabled:
        asyncio.create_task(
            run_orchestrator_scheduler(
                instrument_ids=instrument_ids,
                slow_stores=slow_stores,
                latest_snapshots=latest_snapshots,
                regime_detector=regime_detector,
                redis_client=publisher._redis,
                gate_map=orchestrator_gate_map,
                param_adjustments=orchestrator_param_adj,
                params=orch_params,
            ),
            name="orchestrator_scheduler",
        )
        logger.info("orchestrator_scheduler_task_launched", instruments=instrument_ids)
    else:
        logger.info("orchestrator_scheduler_skipped", reason="disabled in config")

    await consumer.subscribe(
        channels=[Channel.MARKET_SNAPSHOTS],
        group="signals_agent",
        consumer_name="signals-0",
    )

    signal_count = 0
    snapshot_count = 0

    try:
        async for channel, msg_id, payload in consumer.listen():
            try:
                try:
                    snapshot = deserialize_snapshot(payload)
                except (KeyError, ValueError) as e:
                    logger.warning("snapshot_deserialize_error", error=str(e))
                    await consumer.ack(channel, "signals_agent", msg_id)
                    continue

                # Route to the correct per-instrument feature stores
                instrument = snapshot.instrument
                slow_store = slow_stores.get(instrument)
                fast_store = fast_stores.get(instrument)
                if slow_store is None or fast_store is None:
                    await consumer.ack(channel, "signals_agent", msg_id)
                    continue

                slow_sampled = slow_store.update(snapshot)
                fast_sampled = fast_store.update(snapshot)
                snapshot_count += 1

                # Checkpoint stores that just took a new sample
                if slow_sampled:
                    try:
                        await _checkpoint_store(
                            publisher._redis, slow_store, instrument, "slow",
                        )
                    except Exception as exc:
                        logger.warning(
                            "feature_store_checkpoint_failed",
                            instrument=instrument, speed="slow", error=str(exc),
                        )
                if fast_sampled:
                    try:
                        await _checkpoint_store(
                            publisher._redis, fast_store, instrument, "fast",
                        )
                    except Exception as exc:
                        logger.warning(
                            "feature_store_checkpoint_failed",
                            instrument=instrument, speed="fast", error=str(exc),
                        )

                # Classify current session for session-aware overrides
                session_info = classify_session(snapshot.timestamp)

                # Keep latest snapshot and regime current for Claude scheduler
                latest_snapshots[instrument] = snapshot
                regime_detector.update(snapshot)

                # Run this instrument's strategies
                for strategy in strategies_by_instrument.get(instrument, []):
                    store = fast_store if strategy.name in _FAST_STRATEGIES else slow_store
                    if store.sample_count < strategy.min_history:
                        continue

                    # Orchestrator gate map check (ORCH-12) — safe default True
                    if not orchestrator_gate_map.get((instrument, strategy.name), True):
                        continue  # strategy disabled by orchestrator for this instrument

                    # Apply session overrides temporarily
                    overrides = get_session_overrides(
                        session_config, strategy.name, session_info.session_type, instrument,
                    )
                    # Merge orchestrator param adjustments (ORCH-11) — orchestrator wins
                    orch_adj = orchestrator_param_adj.get((instrument, strategy.name), {})
                    if orch_adj:
                        overrides = {**overrides, **orch_adj}
                    originals = _apply_session_overrides(strategy, overrides)

                    try:
                        signals = strategy.evaluate(snapshot, store)
                    except Exception as e:
                        logger.error(
                            "strategy_error",
                            strategy=strategy.name,
                            instrument=instrument,
                            error=str(e),
                        )
                        signals = []
                    finally:
                        # Restore original params after evaluation
                        _restore_params(strategy, originals)

                    for signal in signals:
                        # Apply conviction normalization and unified routing
                        signal = _apply_conviction_normalization(signal)

                        try:
                            await publisher.publish(
                                Channel.SIGNALS,
                                signal_to_dict(signal),
                            )
                        except Exception as exc:
                            logger.error(
                                "signal_publish_failed",
                                signal_id=signal.signal_id,
                                instrument=signal.instrument,
                                error=str(exc),
                                exc_type=type(exc).__name__,
                            )
                            continue
                        try:
                            await repo.write_signal(SignalRecord(
                                signal_id=signal.signal_id,
                                timestamp=signal.timestamp,
                                instrument=signal.instrument,
                                source=signal.source.value,
                                direction=signal.direction.value,
                                conviction=signal.conviction,
                                time_horizon_seconds=int(
                                    signal.time_horizon.total_seconds()
                                ),
                                reasoning=signal.reasoning,
                                entry_price=signal.entry_price,
                            ))
                        except SQLAlchemyError as exc:
                            logger.warning(
                                "signal_db_write_failed",
                                signal_id=signal.signal_id,
                                error=str(exc),
                                exc_type=type(exc).__name__,
                            )
                        except Exception as exc:
                            logger.warning(
                                "signal_db_write_failed",
                                signal_id=signal.signal_id,
                                error=str(exc),
                                exc_type=type(exc).__name__,
                            )
                        signal_count += 1
                        logger.info(
                            "signal_emitted",
                            strategy=strategy.name,
                            instrument=signal.instrument,
                            direction=signal.direction.value,
                            conviction=signal.conviction,
                            conviction_band=signal.metadata.get("conviction_band"),
                            entry=str(signal.entry_price),
                        )

                await consumer.ack(channel, "signals_agent", msg_id)

                if snapshot_count % 500 == 0:
                    slow_counts = {
                        iid: s.sample_count for iid, s in slow_stores.items()
                    }
                    fast_counts = {
                        iid: s.sample_count for iid, s in fast_stores.items()
                    }
                    logger.info(
                        "signals_progress",
                        snapshots=snapshot_count,
                        signals_emitted=signal_count,
                        store_samples_slow=slow_counts,
                        store_samples_fast=fast_counts,
                    )
                    try:
                        # Publish to Redis hash for dashboard visibility
                        await publisher._redis.hset(
                            "phantom:feature_store_status",
                            mapping={
                                f"{k}:slow": str(v) for k, v in slow_counts.items()
                            } | {
                                f"{k}:fast": str(v) for k, v in fast_counts.items()
                            },
                        )
                    except Exception as exc:
                        logger.warning(
                            "feature_store_status_publish_failed",
                            error=str(exc),
                            exc_type=type(exc).__name__,
                        )
            except Exception as e:
                logger.error(
                    "message_processing_failed",
                    error=str(e),
                    exc_type=type(e).__name__,
                )
                # Best-effort ack to avoid re-processing the same bad message
                with contextlib.suppress(Exception):
                    await consumer.ack(channel, "signals_agent", msg_id)
    finally:
        await consumer.close()
        await publisher.close()
        await db_store.close()
        logger.info(
            "signals_stopped",
            snapshots_processed=snapshot_count,
            signals_emitted=signal_count,
        )


def main() -> None:
    """CLI entrypoint."""
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass

    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("signals_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
