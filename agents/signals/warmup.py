"""Historical candle warmup for FeatureStores.

At signals agent startup, fetches historical OHLCV candles from the
Coinbase Advanced REST API and seeds FeatureStores so strategies can
fire immediately instead of waiting hours for live data to accumulate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agents.signals.feature_store import FeatureStore
from libs.coinbase.models import CandleResponse
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.instruments import get_instrument
from libs.common.logging import setup_logging
from libs.common.models.market_snapshot import MarketSnapshot

logger = setup_logging("warmup", json_output=False)

# Coinbase Advanced Trade returns max 300 candles per request.
_MAX_CANDLES_PER_REQUEST = 300

# Mapping from FeatureStore sample_interval to the best Coinbase granularity.
# We pick the largest granularity that fits within the sample interval so each
# candle maps 1:1 to a FeatureStore sample.
_INTERVAL_TO_GRANULARITY: list[tuple[float, str, float]] = [
    # (max_interval_secs, granularity_string, candle_duration_secs)
    (60, "ONE_MINUTE", 60),
    (300, "FIVE_MINUTE", 300),
    (900, "FIFTEEN_MINUTE", 900),
    (3600, "ONE_HOUR", 3600),
    (21600, "SIX_HOUR", 21600),
]


def _pick_granularity(sample_interval_secs: float) -> tuple[str, float]:
    """Choose the best candle granularity for a given sample interval.

    Returns the largest granularity whose duration is <= the sample interval.
    Falls back to ONE_MINUTE if the interval is very small.

    Returns:
        Tuple of (granularity_string, candle_duration_seconds).
    """
    best_gran = "ONE_MINUTE"
    best_dur = 60.0
    for max_interval, gran, dur in _INTERVAL_TO_GRANULARITY:
        if dur <= sample_interval_secs:
            best_gran = gran
            best_dur = dur
        if max_interval >= sample_interval_secs:
            break
    return best_gran, best_dur


def _candle_to_snapshot(
    candle: CandleResponse,
    instrument_id: str,
    open_interest: Decimal = Decimal("0"),
    funding_rate: Decimal = Decimal("0"),
) -> MarketSnapshot:
    """Build a minimal MarketSnapshot from a historical candle.

    Price fields (close, high, low, volume) come from the candle.
    OI and funding rate are backfilled with the current live values
    so z-score baselines aren't polluted by zeros.
    """
    close = Decimal(candle.close)
    volume = Decimal(candle.volume)
    ts = datetime.fromtimestamp(int(candle.start), tz=UTC)

    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument_id,
        mark_price=close,
        index_price=close,  # Best approximation from candle data
        last_price=close,
        best_bid=close,
        best_ask=close,
        spread_bps=0.0,
        volume_24h=volume,
        open_interest=open_interest,
        funding_rate=funding_rate,
        next_funding_time=ts,
        hours_since_last_funding=0.0,
        orderbook_imbalance=0.0,
        volatility_1h=0.0,
        volatility_24h=0.0,
    )


async def _fetch_candles(
    client: CoinbaseRESTClient,
    product_id: str,
    granularity: str,
    candle_duration_secs: float,
    count: int,
) -> list[CandleResponse]:
    """Fetch up to `count` historical candles, paginating if needed.

    Coinbase returns max 300 candles per request. For larger counts,
    we issue multiple requests with non-overlapping time windows.
    Candles are returned in chronological order (oldest first).

    Args:
        client: Coinbase REST client.
        product_id: Product ID (e.g., 'ETH-PERP-INTX').
        granularity: Coinbase granularity string.
        candle_duration_secs: Duration of one candle in seconds.
        count: Desired number of candles.
    """
    now = datetime.now(UTC)
    all_candles: list[CandleResponse] = []
    remaining = count

    # Work backwards from now in chunks of _MAX_CANDLES_PER_REQUEST
    end_time = now
    while remaining > 0:
        batch_size = min(remaining, _MAX_CANDLES_PER_REQUEST)
        start_time = end_time - timedelta(seconds=candle_duration_secs * batch_size)

        candles = await client.get_candles(
            product_id=product_id,
            granularity=granularity,
            start=str(int(start_time.timestamp())),
            end=str(int(end_time.timestamp())),
        )

        if not candles:
            break

        # Coinbase may return candles in descending order — normalize to ascending
        candles.sort(key=lambda c: int(c.start))
        all_candles = candles + all_candles  # Prepend older candles
        remaining -= len(candles)
        end_time = start_time

        # If we got fewer candles than requested, no more data is available
        if len(candles) < batch_size:
            break

    return all_candles[-count:]  # Trim to exactly `count` most recent


async def warmup_feature_store(
    client: CoinbaseRESTClient,
    store: FeatureStore,
    instrument_id: str,
    open_interest: Decimal = Decimal("0"),
    funding_rate: Decimal = Decimal("0"),
) -> int:
    """Warm up a single FeatureStore from historical candles.

    Skips warmup if the store already has sufficient data (e.g., restored
    from a Redis checkpoint). Only fills the gap between current sample
    count and the store's max capacity.

    Args:
        client: Coinbase REST client for fetching candles.
        store: FeatureStore to populate.
        instrument_id: Instrument ID (e.g., 'ETH-PERP').
        open_interest: Current live OI to backfill into warmup samples.
        funding_rate: Current live funding rate to backfill into warmup samples.

    Returns:
        Number of samples added to the store.
    """
    if store.sample_count >= store._max_samples * 0.8:
        logger.info(
            "warmup_skipped_sufficient_data",
            instrument=instrument_id,
            sample_count=store.sample_count,
            max_samples=store._max_samples,
        )
        return 0

    # Determine how many candles we need
    needed = store._max_samples - store.sample_count
    sample_interval_secs = store._sample_interval.total_seconds()
    granularity, candle_duration_secs = _pick_granularity(sample_interval_secs)

    # Resolve product_id from instrument registry
    product_id = get_instrument(instrument_id).product_id

    logger.info(
        "warmup_fetching_candles",
        instrument=instrument_id,
        product_id=product_id,
        granularity=granularity,
        needed=needed,
        current_samples=store.sample_count,
    )

    try:
        candles = await _fetch_candles(
            client=client,
            product_id=product_id,
            granularity=granularity,
            candle_duration_secs=candle_duration_secs,
            count=needed,
        )
    except Exception as exc:
        logger.error(
            "warmup_fetch_failed",
            instrument=instrument_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return 0

    if not candles:
        logger.warning(
            "warmup_no_candles_returned",
            instrument=instrument_id,
            granularity=granularity,
        )
        return 0

    # Feed candles into the store as synthetic snapshots.
    # Temporarily override the sample interval check by injecting directly.
    added = 0
    for candle in candles:
        snapshot = _candle_to_snapshot(
            candle, instrument_id,
            open_interest=open_interest,
            funding_rate=funding_rate,
        )
        # Force-feed: bypass interval gating by setting _last_sample_time far enough back
        store._last_sample_time = snapshot.timestamp - store._sample_interval - timedelta(seconds=1)
        if store.update(snapshot):
            added += 1

    logger.info(
        "warmup_complete",
        instrument=instrument_id,
        candles_fetched=len(candles),
        samples_added=added,
        total_samples=store.sample_count,
    )
    return added


async def warmup_all_stores(
    client: CoinbaseRESTClient,
    slow_stores: dict[str, FeatureStore],
    fast_stores: dict[str, FeatureStore],
) -> dict[str, dict[str, int]]:
    """Warm up all FeatureStores from historical candles.

    Processes each instrument sequentially to respect rate limits.
    Returns a summary of how many samples were added per instrument/speed.

    Args:
        client: Coinbase REST client.
        slow_stores: Per-instrument slow FeatureStores (5-min bars).
        fast_stores: Per-instrument fast FeatureStores (30s bars).

    Returns:
        Dict of {instrument_id: {"slow": N, "fast": N}} with sample counts added.
    """
    results: dict[str, dict[str, int]] = {}

    for instrument_id in slow_stores:
        results[instrument_id] = {}

        # Fetch current OI and funding rate to backfill into warmup samples.
        # This prevents z-score pollution from zeros in OI/funding deques.
        product_id = get_instrument(instrument_id).product_id
        open_interest = Decimal("0")
        funding_rate = Decimal("0")
        try:
            fr_resp = await client.get_funding_rate(product_id)
            open_interest = fr_resp.open_interest
            funding_rate = fr_resp.funding_rate
            logger.info(
                "warmup_live_baseline_fetched",
                instrument=instrument_id,
                open_interest=str(open_interest),
                funding_rate=str(funding_rate),
            )
        except Exception as exc:
            logger.warning(
                "warmup_live_baseline_failed",
                instrument=instrument_id,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        # Warm up slow store (5-min bars → FIVE_MINUTE candles)
        slow_added = await warmup_feature_store(
            client, slow_stores[instrument_id], instrument_id,
            open_interest=open_interest,
            funding_rate=funding_rate,
        )
        results[instrument_id]["slow"] = slow_added

        # Warm up fast store (30s bars → ONE_MINUTE candles, best available)
        if instrument_id in fast_stores:
            fast_added = await warmup_feature_store(
                client, fast_stores[instrument_id], instrument_id,
                open_interest=open_interest,
                funding_rate=funding_rate,
            )
            results[instrument_id]["fast"] = fast_added

    total_slow = sum(r.get("slow", 0) for r in results.values())
    total_fast = sum(r.get("fast", 0) for r in results.values())
    logger.info(
        "warmup_all_complete",
        instruments=len(results),
        total_slow_samples=total_slow,
        total_fast_samples=total_fast,
    )
    return results
