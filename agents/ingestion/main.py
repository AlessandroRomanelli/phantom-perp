"""Ingestion agent entrypoint.

Runs three concurrent data sources:
  1. WebSocket MARKET_DATA listener (real-time ticks for all instruments)
  2. REST candle pollers (1m, 5m, 15m, 1h, 6h) -- all active instruments
  3. REST funding rate pollers (every 5 minutes) -- all active instruments

On every WebSocket tick that updates state, a per-instrument MarketSnapshot
is built (if the instrument is ready) and published to stream:market_snapshots,
throttled to at most 1 snapshot per instrument per 100ms.

After WS reconnect, instruments that do not receive data within
STALE_DATA_HALT_SECONDS (30s) are marked stale and stop publishing
snapshots until fresh data arrives (D-10).
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.coinbase.ws_client import CoinbaseWSClient
from libs.common.config import get_settings
from libs.common.constants import (
    REST_CANDLE_STALE_SECONDS,
    REST_FUNDING_STALE_SECONDS,
    REST_POLLER_STAGGER_SECONDS,
)
from libs.coinbase.product_discovery import discover_and_update_registry
from libs.common.instruments import get_all_instruments
from libs.common.logging import setup_logging
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisPublisher

from agents.ingestion.normalizer import build_snapshot, snapshot_to_dict
from agents.ingestion.sources.candles import run_all_candle_pollers
from agents.ingestion.sources.funding_rate import run_funding_poller
from agents.ingestion.sources.ws_market_data import run_ws_market_data
from agents.ingestion.state import IngestionState

logger = setup_logging("ingestion", json_output=False)


async def _run_rest_poller_isolated(
    coro: Coroutine[Any, Any, None],
    instrument_id: str,
    poller_name: str,
) -> None:
    """Wrap a REST poller to prevent TaskGroup teardown on unexpected crash (D-05)."""
    try:
        await coro
    except Exception as e:
        logger.error(
            "rest_poller_crashed",
            instrument=instrument_id,
            poller=poller_name,
            error=str(e),
            exc_type=type(e).__name__,
        )
        # Do NOT re-raise -- other instruments keep running


def _mark_stale_rest_data(states: dict[str, IngestionState]) -> None:
    """Reset readiness flags if REST data hasn't been updated within thresholds (D-07)."""
    now = datetime.now(UTC)
    for instrument_id, state in states.items():
        if state.has_candles and state.last_candle_update is not None:
            elapsed = (now - state.last_candle_update).total_seconds()
            if elapsed > REST_CANDLE_STALE_SECONDS:
                state.has_candles = False
                logger.warning(
                    "instrument_candles_stale",
                    instrument=instrument_id,
                    elapsed_seconds=round(elapsed, 1),
                )
        if state.has_funding and state.last_funding_update is not None:
            elapsed = (now - state.last_funding_update).total_seconds()
            if elapsed > REST_FUNDING_STALE_SECONDS:
                state.has_funding = False
                logger.warning(
                    "instrument_funding_stale",
                    instrument=instrument_id,
                    elapsed_seconds=round(elapsed, 1),
                )


async def run_agent() -> None:
    """Main entrypoint for the ingestion agent."""
    settings = get_settings()

    # Auth -- ingestion only uses public endpoints (market data, candles,
    # funding rate), so any portfolio's API key works. We use Portfolio A's.
    auth = CoinbaseAuth(
        api_key=settings.coinbase.api_key_a,
        api_secret=settings.coinbase.api_secret_a,
    )

    # Shared rate limiter for all REST clients (D-04, D-09)
    rate_limiter = RateLimiter()

    # D-14: Discover product IDs dynamically at startup
    discovery_client = CoinbaseRESTClient(
        auth=auth,
        base_url=settings.coinbase.rest_url,
        rate_limiter=rate_limiter,
    )
    product_id_map = await discover_and_update_registry(discovery_client)
    logger.info("product_ids_resolved", mapping=product_id_map)

    # Per-instrument shared state (AFTER discovery so product IDs are resolved)
    instruments = get_all_instruments()
    states: dict[str, IngestionState] = {
        inst.id: IngestionState(instrument_id=inst.id)
        for inst in instruments
    }

    # Product ID -> instrument ID mapping for WS dispatch (D-02, D-17)
    product_to_instrument: dict[str, str] = {
        inst.product_id: inst.id for inst in instruments
    }
    # e.g. {"ETH-PERP-INTX": "ETH-PERP", "BTC-PERP-INTX": "BTC-PERP", ...}

    # Per-instrument REST clients with isolated HTTP connection pools (D-08)
    rest_clients: dict[str, CoinbaseRESTClient] = {}
    for inst in instruments:
        rest_clients[inst.id] = CoinbaseRESTClient(
            auth=auth,
            base_url=settings.coinbase.rest_url,
            rate_limiter=rate_limiter,
        )

    # WebSocket client -- Advanced Trade market data is public, no auth needed
    ws_client = CoinbaseWSClient(
        url=settings.coinbase.ws_market_url,
        max_reconnect_delay=30.0,
    )

    # Redis publisher
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    # Snapshot counter for logging
    snapshot_count = 0

    # Per-instrument snapshot throttle (D-05): at most 1 snapshot per instrument per 100ms
    _last_publish: dict[str, float] = {}
    _THROTTLE_SECONDS = 0.1

    async def on_ws_update(instrument_id: str) -> None:
        """Called on every WS state update -- build and publish a throttled snapshot."""
        nonlocal snapshot_count

        # Throttle: at most 1 snapshot per instrument per 100ms (D-05)
        now = time.monotonic()
        if now - _last_publish.get(instrument_id, 0.0) < _THROTTLE_SECONDS:
            return
        _last_publish[instrument_id] = now

        state = states[instrument_id]

        # D-11: Validate instrument ID consistency before snapshot creation
        assert state.instrument_id == instrument_id, (
            f"Instrument ID mismatch: state has {state.instrument_id!r}, "
            f"callback received {instrument_id!r}"
        )

        # Readiness gate: all data sources must have delivered (D-08, D-09)
        # Also gates instruments marked stale after reconnect (D-10) --
        # has_ws_tick is reset to False by _mark_stale_instruments, so
        # is_ready() returns False until fresh WS data arrives.
        if not state.is_ready():
            return

        snapshot = build_snapshot(state, instrument_id=instrument_id)
        if snapshot is None:
            return

        payload = snapshot_to_dict(snapshot)
        await publisher.publish(Channel.MARKET_SNAPSHOTS, payload)

        # Global counter, log every 100th with instrument ID (D-07)
        snapshot_count += 1
        if snapshot_count % 100 == 1:
            logger.info(
                "snapshot_published",
                instrument=instrument_id,
                count=snapshot_count,
                mark_price=str(snapshot.mark_price),
                spread_bps=f"{snapshot.spread_bps:.1f}",
                funding_rate=str(snapshot.funding_rate),
            )

    logger.info("ingestion_starting")

    try:
        async with asyncio.TaskGroup() as tg:
            # 1. WebSocket market data (real-time, all instruments) (MWS-01, MWS-02)
            tg.create_task(
                run_ws_market_data(
                    ws_client,
                    states,
                    product_to_instrument,
                    on_update=on_ws_update,
                ),
            )

            # 2. REST candle pollers -- per-instrument with staggered starts (MPOL-01)
            for i, inst in enumerate(instruments):
                async def _launch_candles(
                    inst_id: str = inst.id,
                    delay: float = i * REST_POLLER_STAGGER_SECONDS,
                ) -> None:
                    await asyncio.sleep(delay)
                    await run_all_candle_pollers(
                        rest_clients[inst_id], states[inst_id], instrument_id=inst_id,
                    )

                tg.create_task(
                    _run_rest_poller_isolated(_launch_candles(), inst.id, "candle_poller"),
                )

            # 3. REST funding rate pollers -- per-instrument with staggered starts (MPOL-02)
            for i, inst in enumerate(instruments):
                async def _launch_funding(
                    inst_id: str = inst.id,
                    delay: float = i * REST_POLLER_STAGGER_SECONDS,
                ) -> None:
                    await asyncio.sleep(delay)
                    await run_funding_poller(
                        rest_clients[inst_id], states[inst_id], publisher,
                        instrument_id=inst_id,
                    )

                tg.create_task(
                    _run_rest_poller_isolated(_launch_funding(), inst.id, "funding_poller"),
                )

            # 4. REST staleness checker -- periodic, analogous to WS staleness (D-07)
            async def _rest_staleness_loop() -> None:
                while True:
                    await asyncio.sleep(30)  # Check every 30s
                    _mark_stale_rest_data(states)

            tg.create_task(_rest_staleness_loop())
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("ingestion_task_failed", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await ws_client.close()
        for client in rest_clients.values():
            await client.close()
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
