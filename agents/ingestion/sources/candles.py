"""REST candle poller for multiple timeframes.

Periodically fetches OHLCV candles from the Coinbase Advanced REST API
for 1m, 5m, 15m, 1h, and 4h granularities, storing them in the
shared IngestionState for volatility computation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError
from libs.common.logging import setup_logging
from libs.common.utils import utc_now

from agents.ingestion.state import IngestionState

logger = setup_logging("candle_poller", json_output=False)


@dataclass(frozen=True)
class TimeframeConfig:
    """Configuration for a single candle timeframe."""

    granularity: str  # Coinbase API granularity string
    poll_interval_seconds: float  # How often to poll
    max_candles: int  # Max candles to retain in state
    candle_duration: timedelta = timedelta(hours=1)  # Duration of one candle


# Timeframes to poll, ordered by frequency
TIMEFRAMES: list[TimeframeConfig] = [
    TimeframeConfig(granularity="ONE_MINUTE", poll_interval_seconds=60, max_candles=60, candle_duration=timedelta(minutes=1)),
    TimeframeConfig(granularity="FIVE_MINUTE", poll_interval_seconds=120, max_candles=60, candle_duration=timedelta(minutes=5)),
    TimeframeConfig(granularity="FIFTEEN_MINUTE", poll_interval_seconds=300, max_candles=48, candle_duration=timedelta(minutes=15)),
    TimeframeConfig(granularity="ONE_HOUR", poll_interval_seconds=600, max_candles=48, candle_duration=timedelta(hours=1)),
    TimeframeConfig(granularity="SIX_HOUR", poll_interval_seconds=1800, max_candles=40, candle_duration=timedelta(hours=6)),
]


async def poll_candles_once(
    rest_client: CoinbaseRESTClient,
    state: IngestionState,
    tf: TimeframeConfig,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Fetch candles for a single timeframe and update state.

    Args:
        rest_client: Coinbase Advanced REST client.
        state: Shared ingestion state.
        tf: Timeframe configuration.
        instrument_id: Instrument to fetch candles for.
    """
    try:
        start = utc_now() - tf.candle_duration * tf.max_candles
        candles = await rest_client.get_candles(
            instrument_id=instrument_id,
            granularity=tf.granularity,
            start=start.isoformat(),
        )
        # Keep only the most recent candles
        state.candles_by_granularity[tf.granularity] = candles[-tf.max_candles :]
        state.last_candle_update = utc_now()
        if not state.has_candles:
            state.has_candles = True
        logger.debug(
            "candles_fetched",
            instrument=instrument_id,
            granularity=tf.granularity,
            count=len(candles),
        )
    except RateLimitExceededError:
        logger.warning("candle_poll_rate_limited", instrument=instrument_id, granularity=tf.granularity)
    except CoinbaseAPIError as e:
        logger.error("candle_poll_error", instrument=instrument_id, granularity=tf.granularity, error=str(e))
    except Exception as e:
        logger.error("candle_poll_unexpected_error", instrument=instrument_id, granularity=tf.granularity, error=str(e), exc_type=type(e).__name__)


async def run_candle_poller(
    rest_client: CoinbaseRESTClient,
    state: IngestionState,
    tf: TimeframeConfig,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Continuously poll candles for a single timeframe.

    Runs an initial fetch immediately, then polls at the configured interval.

    Args:
        rest_client: Coinbase Advanced REST client.
        state: Shared ingestion state.
        tf: Timeframe configuration.
        instrument_id: Instrument to fetch candles for.
    """
    # Initial fetch
    await poll_candles_once(rest_client, state, tf, instrument_id)
    consecutive_failures = 0

    while True:
        await asyncio.sleep(tf.poll_interval_seconds)
        before = state.last_candle_update
        await poll_candles_once(rest_client, state, tf, instrument_id)
        if state.last_candle_update == before:
            consecutive_failures += 1
            if consecutive_failures >= 5:
                logger.warning(
                    "candle_poller_consecutive_failures",
                    instrument=instrument_id,
                    granularity=tf.granularity,
                    count=consecutive_failures,
                )
        else:
            consecutive_failures = 0


async def run_all_candle_pollers(
    rest_client: CoinbaseRESTClient,
    state: IngestionState,
    instrument_id: str = "ETH-PERP",
) -> None:
    """Launch candle pollers for all configured timeframes.

    Runs all timeframe pollers concurrently using a TaskGroup.

    Args:
        rest_client: Coinbase INTX REST client.
        state: Shared ingestion state.
        instrument_id: Instrument to fetch candles for.
    """
    async with asyncio.TaskGroup() as tg:
        for tf in TIMEFRAMES:
            tg.create_task(run_candle_poller(rest_client, state, tf, instrument_id))
