"""Signal generation agent entrypoint.

Consumes MarketSnapshots from stream:market_snapshots, routes them to
per-instrument FeatureStores, runs per-instrument strategy instances
(each with their own parameter overrides), and publishes resulting
StandardSignals to stream:signals.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import yaml

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
from libs.common.models.signal import StandardSignal
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.signals.conviction_normalizer import normalize_conviction, should_route_portfolio_a
from agents.signals.feature_store import FeatureStore
from agents.signals.session_classifier import SessionType, classify_session
from agents.signals.strategies.base import SignalStrategy
from agents.signals.strategies.correlation import CorrelationParams, CorrelationStrategy
from agents.signals.strategies.liquidation_cascade import (
    LiquidationCascadeParams,
    LiquidationCascadeStrategy,
)
from agents.signals.strategies.mean_reversion import MeanReversionParams, MeanReversionStrategy
from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy
from agents.signals.strategies.orderbook_imbalance import (
    OrderbookImbalanceParams,
    OrderbookImbalanceStrategy,
)
from agents.signals.strategies.regime_trend import RegimeTrendParams, RegimeTrendStrategy
from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

logger = setup_logging("signals", json_output=False)

# Strategy name -> class mapping.  Add new strategies here.
STRATEGY_CLASSES: dict[str, type[SignalStrategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "liquidation_cascade": LiquidationCascadeStrategy,
    "correlation": CorrelationStrategy,
    "regime_trend": RegimeTrendStrategy,
    "orderbook_imbalance": OrderbookImbalanceStrategy,
    "vwap": VWAPStrategy,
}

STRATEGY_PARAMS_CLASSES: dict[str, type] = {
    "momentum": MomentumParams,
    "mean_reversion": MeanReversionParams,
    "liquidation_cascade": LiquidationCascadeParams,
    "correlation": CorrelationParams,
    "regime_trend": RegimeTrendParams,
    "orderbook_imbalance": OrderbookImbalanceParams,
    "vwap": VWAPParams,
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
        volatility_1h=float(payload["volatility_1h"]),
        volatility_24h=float(payload["volatility_24h"]),
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

    # Per-instrument feature stores
    stores: dict[str, FeatureStore] = {
        iid: FeatureStore(sample_interval=timedelta(seconds=30))
        for iid in instrument_ids
    }

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
                snapshot = deserialize_snapshot(payload)
            except (KeyError, ValueError) as e:
                logger.warning("snapshot_deserialize_error", error=str(e))
                await consumer.ack(channel, "signals_agent", msg_id)
                continue

            # Route to the correct per-instrument feature store
            instrument = snapshot.instrument
            store = stores.get(instrument)
            if store is None:
                await consumer.ack(channel, "signals_agent", msg_id)
                continue

            store.update(snapshot)
            snapshot_count += 1

            # Classify current session for session-aware overrides
            session_info = classify_session(snapshot.timestamp)

            # Run this instrument's strategies
            for strategy in strategies_by_instrument.get(instrument, []):
                if store.sample_count < strategy.min_history:
                    continue

                # Apply session overrides temporarily
                overrides = get_session_overrides(
                    session_config, strategy.name, session_info.session_type, instrument,
                )
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

                    await publisher.publish(
                        Channel.SIGNALS,
                        signal_to_dict(signal),
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
                logger.info(
                    "signals_progress",
                    snapshots=snapshot_count,
                    signals_emitted=signal_count,
                    store_samples={
                        iid: s.sample_count for iid, s in stores.items()
                    },
                )
    finally:
        await consumer.close()
        await publisher.close()
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
