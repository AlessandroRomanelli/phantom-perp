"""Signal generation agent entrypoint.

Consumes MarketSnapshots from stream:market_snapshots, feeds them to
all enabled strategies in parallel, and publishes resulting
StandardSignals to stream:signals.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.config import get_settings, load_strategy_config
from libs.common.constants import INSTRUMENT_ID
from libs.common.logging import setup_logging
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy
from agents.signals.strategies.correlation import CorrelationStrategy
from agents.signals.strategies.liquidation_cascade import LiquidationCascadeStrategy
from agents.signals.strategies.mean_reversion import MeanReversionStrategy
from agents.signals.strategies.momentum import MomentumStrategy
from agents.signals.strategies.regime_trend import RegimeTrendStrategy

logger = setup_logging("signals", json_output=False)


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


def build_strategies() -> list[SignalStrategy]:
    """Instantiate all enabled strategies."""
    strategies: list[SignalStrategy] = []

    momentum_config = load_strategy_config("momentum")
    strategies.append(MomentumStrategy(config=momentum_config))

    mr_config = load_strategy_config("mean_reversion")
    strategies.append(MeanReversionStrategy(config=mr_config))

    liq_config = load_strategy_config("liquidation_cascade")
    strategies.append(LiquidationCascadeStrategy(config=liq_config))

    corr_config = load_strategy_config("correlation")
    strategies.append(CorrelationStrategy(config=corr_config))

    rt_config = load_strategy_config("regime_trend")
    strategies.append(RegimeTrendStrategy(config=rt_config))

    return [s for s in strategies if s.enabled]


async def run_agent() -> None:
    """Main event loop for the signal generation agent."""
    settings = get_settings()

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)
    store = FeatureStore(sample_interval=timedelta(seconds=30))
    strategies = build_strategies()

    logger.info(
        "signals_starting",
        strategies=[s.name for s in strategies],
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

            # Update feature store
            store.update(snapshot)
            snapshot_count += 1

            # Run all strategies
            for strategy in strategies:
                if store.sample_count < strategy.min_history:
                    continue

                try:
                    signals = strategy.evaluate(snapshot, store)
                except Exception as e:
                    logger.error(
                        "strategy_error",
                        strategy=strategy.name,
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
                    feature_samples=store.sample_count,
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
