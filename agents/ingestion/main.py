"""Ingestion agent entrypoint.

Runs three concurrent data sources:
  1. WebSocket MARKET_DATA listener (real-time ticks)
  2. REST candle pollers (1m, 5m, 15m, 1h, 6h)
  3. REST funding rate poller (every 5 minutes)

On every WebSocket tick that updates state, a MarketSnapshot is built
and published to stream:market_snapshots.
"""

from __future__ import annotations

import asyncio
import sys

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.coinbase.ws_client import CoinbaseWSClient
from libs.common.config import get_settings
from libs.common.logging import setup_logging
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisPublisher

from agents.ingestion.normalizer import build_snapshot, snapshot_to_dict
from agents.ingestion.sources.candles import run_all_candle_pollers
from agents.ingestion.sources.funding_rate import run_funding_poller
from agents.ingestion.sources.ws_market_data import run_ws_market_data
from agents.ingestion.state import IngestionState

logger = setup_logging("ingestion", json_output=False)


async def run_agent() -> None:
    """Main entrypoint for the ingestion agent."""
    settings = get_settings()

    # Shared state across all sources
    state = IngestionState()

    # Auth — ingestion only uses public endpoints (market data, candles,
    # funding rate), so any portfolio's API key works. We use Portfolio A's.
    auth = CoinbaseAuth(
        api_key=settings.coinbase.api_key_a,
        api_secret=settings.coinbase.api_secret_a,
        passphrase=settings.coinbase.passphrase_a,
    )

    # REST client (with its own rate limiter)
    rest_client = CoinbaseRESTClient(
        auth=auth,
        base_url=settings.coinbase.rest_url,
        rate_limiter=RateLimiter(),
    )

    # WebSocket client — Advanced Trade market data is public, no auth needed
    ws_client = CoinbaseWSClient(
        url=settings.coinbase.ws_market_url,
        max_reconnect_delay=30.0,
    )

    # Redis publisher
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    # Snapshot counter for logging
    snapshot_count = 0

    async def on_ws_update() -> None:
        """Called on every WS state update — build and publish a snapshot."""
        nonlocal snapshot_count
        snapshot = build_snapshot(state)
        if snapshot is None:
            return

        payload = snapshot_to_dict(snapshot)
        await publisher.publish(Channel.MARKET_SNAPSHOTS, payload)

        snapshot_count += 1
        if snapshot_count % 100 == 1:
            logger.info(
                "snapshot_published",
                count=snapshot_count,
                mark_price=str(snapshot.mark_price),
                spread_bps=f"{snapshot.spread_bps:.1f}",
                funding_rate=str(snapshot.funding_rate),
            )

    logger.info("ingestion_starting")

    try:
        async with asyncio.TaskGroup() as tg:
            # 1. WebSocket market data (real-time)
            tg.create_task(
                run_ws_market_data(ws_client, state, on_update=on_ws_update),
            )
            # 2. REST candle pollers (all timeframes)
            tg.create_task(
                run_all_candle_pollers(rest_client, state),
            )
            # 3. REST funding rate poller
            tg.create_task(
                run_funding_poller(rest_client, state, publisher),
            )
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("ingestion_task_failed", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await ws_client.close()
        await rest_client.close()
        await publisher.close()
        logger.info("ingestion_stopped", snapshots_published=snapshot_count)


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
        logger.info("ingestion_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
