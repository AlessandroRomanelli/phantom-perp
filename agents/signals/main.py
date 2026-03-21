"""Signal generation agent entrypoint.

Consumes MarketSnapshots from stream:market_snapshots, routes them to
per-instrument FeatureStores, runs per-instrument strategy instances
(each with their own parameter overrides), and publishes resulting
StandardSignals to stream:signals.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.config import (
    get_settings,
    load_strategy_config,
    load_strategy_config_for_instrument,
    log_config_diff,
    validate_strategy_config,
)
from libs.common.constants import ACTIVE_INSTRUMENT_IDS
from libs.common.logging import setup_logging
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy
from agents.signals.strategies.correlation import CorrelationParams, CorrelationStrategy
from agents.signals.strategies.liquidation_cascade import (
    LiquidationCascadeParams,
    LiquidationCascadeStrategy,
)
from agents.signals.strategies.mean_reversion import MeanReversionParams, MeanReversionStrategy
from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy
from agents.signals.strategies.regime_trend import RegimeTrendParams, RegimeTrendStrategy

logger = setup_logging("signals", json_output=False)

# Strategy name → class mapping.  Add new strategies here.
STRATEGY_CLASSES: dict[str, type[SignalStrategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "liquidation_cascade": LiquidationCascadeStrategy,
    "correlation": CorrelationStrategy,
    "regime_trend": RegimeTrendStrategy,
}

STRATEGY_PARAMS_CLASSES: dict[str, type] = {
    "momentum": MomentumParams,
    "mean_reversion": MeanReversionParams,
    "liquidation_cascade": LiquidationCascadeParams,
    "correlation": CorrelationParams,
    "regime_trend": RegimeTrendParams,
}


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


def signal_to_dict(signal: StandardSignal) -> dict[str, Any]:
    """Serialize a StandardSignal to a JSON-compatible dict for Redis."""
    return {
        "signal_id": signal.signal_id,
        "timestamp": signal.timestamp.isoformat(),
        "instrument": signal.instrument,
        "direction": signal.direction.value,
        "conviction": signal.conviction,
        "source": signal.source.value,
        "time_horizon_seconds": int(signal.time_horizon.total_seconds()),
        "reasoning": signal.reasoning,
        "suggested_target": signal.suggested_target.value if signal.suggested_target else None,
        "entry_price": str(signal.entry_price) if signal.entry_price else None,
        "stop_loss": str(signal.stop_loss) if signal.stop_loss else None,
        "take_profit": str(signal.take_profit) if signal.take_profit else None,
        "metadata": signal.metadata,
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

    for strategy_name, strategy_cls in STRATEGY_CLASSES.items():
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
    instrument_ids = yaml_instruments if yaml_instruments else ACTIVE_INSTRUMENT_IDS

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

            # Run this instrument's strategies
            for strategy in strategies_by_instrument.get(instrument, []):
                if store.sample_count < strategy.min_history:
                    continue

                try:
                    signals = strategy.evaluate(snapshot, store)
                except Exception as e:
                    logger.error(
                        "strategy_error",
                        strategy=strategy.name,
                        instrument=instrument,
                        error=str(e),
                    )
                    continue

                for signal in signals:
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
