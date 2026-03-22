"""REST funding rate poller.

Fetches the current hourly funding rate for each active instrument from the
Coinbase Advanced REST API. Polls every 5 minutes (the rate changes slowly
but we want reasonably fresh data for the signal pipeline).

Also publishes FundingRate updates to stream:funding_updates for
consumption by the risk agent and funding_arb strategy.
"""

from __future__ import annotations

import asyncio
from typing import Any

from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError
from libs.common.logging import setup_logging
from libs.common.utils import utc_now
from libs.messaging.base import Publisher
from libs.messaging.channels import Channel

from agents.ingestion.state import IngestionState

logger = setup_logging("funding_poller", json_output=False)

FUNDING_POLL_INTERVAL_SECONDS = 300  # 5 minutes


async def poll_funding_once(
    rest_client: CoinbaseRESTClient,
    state: IngestionState,
    publisher: Publisher | None = None,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Fetch the current funding rate and update state.

    Args:
        rest_client: Coinbase Advanced REST client.
        state: Shared ingestion state.
        publisher: Optional publisher to emit FundingRate updates.
        instrument_id: Instrument to fetch funding rate for.
    """
    try:
        resp = await rest_client.get_funding_rate(instrument_id=instrument_id)

        state.funding_rate = resp.funding_rate
        state.next_funding_time = resp.event_time
        state.funding_mark_price = resp.mark_price
        state.funding_index_price = None  # Not in the response; use WS index
        state.last_funding_update = utc_now()
        if not state.has_funding:
            state.has_funding = True

        # Advanced Trade WS ticker doesn't provide mark_price/index_price.
        # Always update from the funding endpoint (best available source).
        state.mark_price = resp.mark_price
        if state.index_price is None and state.last_price is not None:
            state.index_price = state.last_price

        logger.debug(
            "funding_fetched",
            instrument=instrument_id,
            rate=str(resp.funding_rate),
            event_time=resp.event_time.isoformat(),
            mark_price=str(resp.mark_price),
        )

        if publisher is not None:
            await _publish_funding_update(publisher, resp, instrument_id)

    except RateLimitExceededError:
        logger.warning("funding_poll_rate_limited", instrument=instrument_id)
    except CoinbaseAPIError as e:
        logger.error("funding_poll_error", instrument=instrument_id, error=str(e))
    except Exception as e:
        logger.error("funding_poll_unexpected_error", instrument=instrument_id, error=str(e), exc_type=type(e).__name__)


async def _publish_funding_update(
    publisher: Publisher,
    resp: Any,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Publish a funding rate update to the funding_updates stream."""
    now = utc_now()
    payload = {
        "timestamp": now.isoformat(),
        "instrument": instrument_id,
        "rate": str(resp.funding_rate),
        "next_settlement_time": resp.event_time.isoformat(),
        "mark_price": str(resp.mark_price),
    }
    await publisher.publish(Channel.FUNDING_UPDATES, payload)


async def run_funding_poller(
    rest_client: CoinbaseRESTClient,
    state: IngestionState,
    publisher: Publisher | None = None,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Continuously poll the funding rate.

    Args:
        rest_client: Coinbase Advanced REST client.
        state: Shared ingestion state.
        publisher: Optional publisher for funding update stream.
        instrument_id: Instrument to fetch funding rate for.
    """
    # Initial fetch
    await poll_funding_once(rest_client, state, publisher, instrument_id)
    consecutive_failures = 0

    while True:
        await asyncio.sleep(FUNDING_POLL_INTERVAL_SECONDS)
        before = state.last_funding_update
        await poll_funding_once(rest_client, state, publisher, instrument_id)
        if state.last_funding_update == before:
            consecutive_failures += 1
            if consecutive_failures >= 5:
                logger.warning(
                    "funding_poller_consecutive_failures",
                    instrument=instrument_id,
                    count=consecutive_failures,
                )
        else:
            consecutive_failures = 0
