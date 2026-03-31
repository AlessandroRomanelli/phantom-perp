"""Alpha combination agent entrypoint.

Consumes StandardSignals from stream:signals and MarketSnapshots from
stream:market_snapshots.  Combines signals, resolves conflicts, routes
to portfolios, and publishes RankedTradeIdeas to the portfolio-scoped
ranked_ideas streams consumed by the risk agent.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.config import get_settings, load_yaml_config
from libs.common.logging import setup_logging
from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from libs.portfolio.router import PortfolioRouter

from agents.alpha.combiner import AlphaCombiner
from agents.alpha.regime_detector import RegimeDetector
from agents.alpha.scorecard import StrategyScorecard

logger = setup_logging("alpha", json_output=False)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


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


def deserialize_signal(payload: dict[str, Any]) -> StandardSignal:
    """Rebuild a StandardSignal from a Redis stream payload dict."""
    suggested = payload.get("suggested_route")
    return StandardSignal(
        signal_id=payload["signal_id"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        direction=PositionSide(payload["direction"]),
        conviction=float(payload["conviction"]),
        source=SignalSource(payload["source"]),
        time_horizon=timedelta(seconds=float(payload["time_horizon_seconds"])),
        reasoning=payload.get("reasoning", ""),
        suggested_route=Route(suggested) if suggested else None,
        entry_price=Decimal(payload["entry_price"]) if payload.get("entry_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        metadata=payload.get("metadata", {}),
    )


def idea_to_dict(idea: RankedTradeIdea) -> dict[str, Any]:
    """Serialize a RankedTradeIdea for Redis.

    Format must match agents.risk.main.deserialize_idea().
    """
    return {
        "idea_id": idea.idea_id,
        "timestamp": idea.timestamp.isoformat(),
        "instrument": idea.instrument,
        "route": idea.route.value,
        "direction": idea.direction.value,
        "conviction": idea.conviction,
        "sources": ",".join(s.value for s in idea.sources),
        "time_horizon_seconds": int(idea.time_horizon.total_seconds()),
        "entry_price": str(idea.entry_price) if idea.entry_price else None,
        "stop_loss": str(idea.stop_loss) if idea.stop_loss else None,
        "take_profit": str(idea.take_profit) if idea.take_profit else None,
        "reasoning": idea.reasoning,
    }


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


async def run_agent() -> None:
    """Main event loop for the alpha combination agent."""
    settings = get_settings()
    config = load_yaml_config()

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    routing_config = config.get("portfolio", {}).get("routing")
    router = PortfolioRouter(config=routing_config)
    regime_detector = RegimeDetector()
    scorecard = StrategyScorecard()
    combiner = AlphaCombiner(
        router=router,
        regime_detector=regime_detector,
        scorecard=scorecard,
    )

    logger.info("alpha_starting")

    await consumer.subscribe(
        channels=[Channel.SIGNALS, Channel.MARKET_SNAPSHOTS],
        group="alpha_agent",
        consumer_name="alpha-0",
    )

    idea_count = 0
    signal_count = 0
    snapshot_count = 0

    try:
        async for channel, msg_id, payload in consumer.listen():
            if channel == Channel.MARKET_SNAPSHOTS:
                try:
                    snapshot = deserialize_snapshot(payload)
                except (KeyError, ValueError) as e:
                    logger.warning("snapshot_deserialize_error", error=str(e))
                    await consumer.ack(channel, "alpha_agent", msg_id)
                    continue
                try:
                    regime_detector.update(snapshot)
                except Exception as e:
                    logger.warning(
                        "regime_update_error",
                        instrument=snapshot.instrument,
                        error=str(e),
                        exc_type=type(e).__name__,
                    )
                snapshot_count += 1
            elif channel == Channel.SIGNALS:
                try:
                    signal = deserialize_signal(payload)
                except (KeyError, ValueError) as e:
                    logger.warning("signal_deserialize_error", error=str(e))
                    await consumer.ack(channel, "alpha_agent", msg_id)
                    continue

                signal_count += 1
                try:
                    ideas = combiner.add_signal(signal)
                except Exception as e:
                    logger.warning(
                        "signal_combination_error",
                        signal_id=signal.signal_id,
                        instrument=signal.instrument,
                        error=str(e),
                        exc_type=type(e).__name__,
                    )
                    await consumer.ack(channel, "alpha_agent", msg_id)
                    continue

                for idea in ideas:
                    target_channel = Channel.ranked_ideas(idea.route)
                    await publisher.publish(target_channel, idea_to_dict(idea))
                    idea_count += 1
                    logger.info(
                        "idea_emitted",
                        direction=idea.direction.value,
                        route=idea.route.value,
                        conviction=f"{idea.conviction:.2f}",
                        sources=",".join(s.value for s in idea.sources),
                        regime=regime_detector.current_regime.value,
                    )

            await consumer.ack(channel, "alpha_agent", msg_id)

            if (signal_count + snapshot_count) % 500 == 0 and (
                signal_count + snapshot_count
            ) > 0:
                logger.info(
                    "alpha_progress",
                    signals=signal_count,
                    snapshots=snapshot_count,
                    ideas_emitted=idea_count,
                    regime=regime_detector.current_regime.value,
                )
    finally:
        await consumer.close()
        await publisher.close()
        logger.info(
            "alpha_stopped",
            signals_processed=signal_count,
            ideas_emitted=idea_count,
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
        logger.info("alpha_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
