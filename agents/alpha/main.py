"""Alpha combination agent entrypoint.

Consumes StandardSignals from stream:signals and MarketSnapshots from
stream:market_snapshots.  Combines signals, resolves conflicts, routes
to portfolios, and publishes RankedTradeIdeas to the portfolio-scoped
ranked_ideas streams consumed by the risk agent.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from typing import Any

from agents.alpha.combiner import AlphaCombiner
from agents.alpha.regime_detector import RegimeDetector
from agents.alpha.scorecard import StrategyScorecard
from libs.common.config import get_settings, load_yaml_config
from libs.common.logging import setup_logging
from libs.common.models.enums import MarketRegime, Route, SignalSource
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from libs.common.serialization import deserialize_signal, deserialize_snapshot, idea_to_dict
from libs.portfolio.router import RouteRouter

logger = setup_logging("alpha", json_output=False)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


async def _mirror_regime_to_redis(
    regimes: dict[str, MarketRegime],
    redis_client: Any,
) -> None:
    """Write per-instrument regime classifications to Redis as a hash (fire-and-forget).

    Key: ``phantom:regime``
    Field: instrument ID (e.g. ``ETH-PERP``)
    Value: regime string (e.g. ``trending_up``)

    Args:
        regimes: Current per-instrument regime mapping.
        redis_client: Async Redis client.
    """
    if not regimes:
        return

    mapping: dict[str, str] = {inst: regime.value for inst, regime in regimes.items()}

    try:
        await redis_client.hset("phantom:regime", mapping=mapping)
    except Exception as exc:
        logger.warning(
            "regime_redis_write_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
        )



async def run_agent() -> None:
    """Main event loop for the alpha combination agent."""
    settings = get_settings()
    config = load_yaml_config()

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    routing_config = config.get("portfolio", {}).get("routing")
    router = RouteRouter(config=routing_config)
    regime_detector = RegimeDetector()
    scorecard = StrategyScorecard()
    alpha_config = config.get("alpha", {})
    exempt_sources_raw = alpha_config.get("exempt_sources", [])
    exempt_sources = {SignalSource(s) for s in exempt_sources_raw} if exempt_sources_raw else None
    combiner = AlphaCombiner(
        router=router,
        regime_detector=regime_detector,
        scorecard=scorecard,
        combination_window=timedelta(seconds=alpha_config.get("combination_window_seconds", 60)),
        min_agreeing_sources=alpha_config.get("min_agreeing_sources", 1),
        exempt_sources=exempt_sources,
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
                    await _mirror_regime_to_redis(regime_detector.regimes, publisher._redis)
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
