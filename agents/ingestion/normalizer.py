"""Build MarketSnapshot from shared IngestionState.

The normalizer is the single function responsible for reading the current
ingestion state and producing a canonical MarketSnapshot that downstream
agents (signals, risk, monitoring) consume.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.serialization import snapshot_to_dict
from libs.common.utils import utc_now

from agents.ingestion.enrichment import (
    compute_orderbook_imbalance,
    compute_spread_bps,
    compute_volatility_1h,
    compute_volatility_24h,
)
from agents.ingestion.state import IngestionState

_ZERO = Decimal("0")


def build_snapshot(
    state: IngestionState,
    instrument_id: str | None = None,
) -> MarketSnapshot | None:
    """Build a MarketSnapshot from the current ingestion state.

    Returns None if the state does not have enough data (e.g., before
    the first WebSocket tick arrives).

    Args:
        state: Current shared ingestion state.
        instrument_id: Optional cross-check parameter. When provided,
            asserts that state.instrument_id matches, catching ID
            corruption bugs early (D-12).

    Returns:
        A frozen MarketSnapshot, or None if data is insufficient.
    """
    if not state.has_minimum_data():
        return None

    if instrument_id is not None:
        assert state.instrument_id == instrument_id, (
            f"Instrument ID mismatch in build_snapshot: "
            f"state={state.instrument_id!r}, param={instrument_id!r}"
        )

    assert state.best_bid is not None
    assert state.best_ask is not None
    assert state.last_price is not None

    # mark_price: Advanced Trade WS doesn't provide a mark_price field.
    # Use last_price from WS (real-time) as the primary source, with
    # funding_mark_price (REST, every 5min) as fallback when WS hasn't ticked.
    mark_price = state.last_price or state.funding_mark_price or state.mark_price
    # index_price: use exchange value when available; Decimal("0") sentinel when not.
    # Downstream strategies guard against the zero sentinel rather than silently
    # using last_price as a substitute, which would make basis calculations meaningless.
    index_price = state.index_price if state.index_price is not None else Decimal("0")
    assert mark_price is not None

    # candle_volume_1m: use latest 1-minute candle volume as per-bar weight for VWAP.
    latest_1m = state.candles_by_granularity.get("ONE_MINUTE", [])
    candle_volume_1m = Decimal(latest_1m[-1].volume) if latest_1m else Decimal("0")

    now = utc_now()

    # Funding fields: fall back to defaults if not yet polled
    funding_rate = state.funding_rate or _ZERO
    next_funding_time = state.next_funding_time or _next_hour(now)
    hours_since = _hours_since_last_funding(now, next_funding_time)

    return MarketSnapshot(
        timestamp=now,
        instrument=state.instrument_id,
        mark_price=mark_price,
        index_price=index_price,
        last_price=state.last_price,
        best_bid=state.best_bid,
        best_ask=state.best_ask,
        spread_bps=compute_spread_bps(state.best_bid, state.best_ask),
        volume_24h=state.volume_24h or _ZERO,
        open_interest=state.open_interest or _ZERO,
        funding_rate=funding_rate,
        next_funding_time=next_funding_time,
        hours_since_last_funding=hours_since,
        orderbook_imbalance=compute_orderbook_imbalance(
            state.bid_depth, state.ask_depth
        ),
        volatility_1h=compute_volatility_1h(state),
        volatility_24h=compute_volatility_24h(state),
        candle_volume_1m=candle_volume_1m,
    )


def _next_hour(now: datetime) -> datetime:
    """Return the start of the next hour from now."""
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _hours_since_last_funding(now: datetime, next_funding: datetime) -> float:
    """Compute hours since the last funding settlement.

    Funding settles every hour. The last settlement was one hour before
    the next settlement time.

    Args:
        now: Current UTC time.
        next_funding: Next scheduled funding settlement time.

    Returns:
        Fractional hours since last settlement, clamped to [0, 1].
    """
    last_settlement = next_funding - timedelta(hours=1)
    elapsed = (now - last_settlement).total_seconds() / 3600.0
    return max(0.0, min(1.0, elapsed))
