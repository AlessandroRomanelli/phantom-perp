"""Build MarketSnapshot from shared IngestionState.

The normalizer is the single function responsible for reading the current
ingestion state and producing a canonical MarketSnapshot that downstream
agents (signals, risk, monitoring) consume.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.constants import INSTRUMENT_ID
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.utils import utc_now

from agents.ingestion.enrichment import (
    compute_orderbook_imbalance,
    compute_spread_bps,
    compute_volatility_1h,
    compute_volatility_24h,
)
from agents.ingestion.state import IngestionState

_ZERO = Decimal("0")


def build_snapshot(state: IngestionState) -> MarketSnapshot | None:
    """Build a MarketSnapshot from the current ingestion state.

    Returns None if the state does not have enough data (e.g., before
    the first WebSocket tick arrives).

    Args:
        state: Current shared ingestion state.

    Returns:
        A frozen MarketSnapshot, or None if data is insufficient.
    """
    if not state.has_minimum_data():
        return None

    assert state.best_bid is not None
    assert state.best_ask is not None
    assert state.last_price is not None

    # mark_price: prefer funding endpoint (updates every 5min) since
    # Advanced Trade WS doesn't provide mark_price
    mark_price = state.funding_mark_price or state.mark_price
    # index_price: prefer WS value, fall back to last_price (spot approx)
    index_price = state.index_price or state.last_price
    assert mark_price is not None
    assert index_price is not None

    now = utc_now()

    # Funding fields: fall back to defaults if not yet polled
    funding_rate = state.funding_rate or _ZERO
    next_funding_time = state.next_funding_time or _next_hour(now)
    hours_since = _hours_since_last_funding(now, next_funding_time)

    return MarketSnapshot(
        timestamp=now,
        instrument=INSTRUMENT_ID,
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
    )


def snapshot_to_dict(snapshot: MarketSnapshot) -> dict[str, Any]:
    """Serialize a MarketSnapshot to a JSON-compatible dict for Redis.

    Converts Decimal fields to strings and datetime to ISO 8601.

    Args:
        snapshot: The MarketSnapshot to serialize.

    Returns:
        Dict ready for orjson serialization.
    """
    return {
        "timestamp": snapshot.timestamp.isoformat(),
        "instrument": snapshot.instrument,
        "mark_price": str(snapshot.mark_price),
        "index_price": str(snapshot.index_price),
        "last_price": str(snapshot.last_price),
        "best_bid": str(snapshot.best_bid),
        "best_ask": str(snapshot.best_ask),
        "spread_bps": snapshot.spread_bps,
        "volume_24h": str(snapshot.volume_24h),
        "open_interest": str(snapshot.open_interest),
        "funding_rate": str(snapshot.funding_rate),
        "next_funding_time": snapshot.next_funding_time.isoformat(),
        "hours_since_last_funding": snapshot.hours_since_last_funding,
        "orderbook_imbalance": snapshot.orderbook_imbalance,
        "volatility_1h": snapshot.volatility_1h,
        "volatility_24h": snapshot.volatility_24h,
    }


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
