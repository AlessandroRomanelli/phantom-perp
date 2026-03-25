"""Metrics engine: VWAP aggregation, FIFO round-trip pairing, and P&L computation.

Stubs only -- full implementation follows in the GREEN phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from libs.storage.repository import AttributedFill


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Aggregated result for a single order (potentially multiple partial fills).

    Partial fills for the same order_id are VWAP-aggregated into a single OrderResult.
    """

    order_id: str
    instrument: str
    primary_source: str
    side: str  # "BUY" or "SELL"
    avg_price: Decimal
    total_size: Decimal
    total_fee: Decimal
    filled_at: datetime  # latest fill timestamp


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A completed round-trip trade (entry + exit pairing).

    Entry and exit orders are paired via FIFO matching. Open positions
    (unmatched entries) are excluded per D-04.
    """

    entry_order_id: str
    exit_order_id: str
    instrument: str
    primary_source: str
    side: str  # entry side: "BUY" (LONG) or "SELL" (SHORT)
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    gross_pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    opened_at: datetime
    closed_at: datetime


def vwap_aggregate(fills: list[AttributedFill]) -> OrderResult:
    """VWAP-aggregate partial fills for a single order into an OrderResult.

    Args:
        fills: Non-empty list of fills for the same order_id.

    Returns:
        OrderResult with VWAP average price and summed size/fees.

    Raises:
        NotImplementedError: Stub — not yet implemented.
    """
    raise NotImplementedError


def pair_round_trips(orders: list[OrderResult]) -> list[RoundTrip]:
    """Pair entry and exit orders into closed RoundTrip objects via FIFO matching.

    Args:
        orders: List of OrderResult objects sorted by filled_at ascending.

    Returns:
        List of closed RoundTrip objects. Unmatched entries (open positions)
        are excluded per D-04.

    Raises:
        NotImplementedError: Stub — not yet implemented.
    """
    raise NotImplementedError


def build_round_trips(
    fills: list[AttributedFill],
) -> dict[tuple[str, str], list[RoundTrip]]:
    """Build round-trips from raw fills, grouped by (primary_source, instrument).

    Groups fills by (primary_source, instrument), VWAP-aggregates per order_id,
    sorts chronologically, and pairs via FIFO matching.

    Args:
        fills: List of AttributedFill records.

    Returns:
        Dict keyed by (primary_source, instrument) with list of RoundTrip values.
        Keys with no completed round-trips are excluded.

    Raises:
        NotImplementedError: Stub — not yet implemented.
    """
    raise NotImplementedError
