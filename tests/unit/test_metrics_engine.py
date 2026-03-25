"""Unit tests for libs/metrics/engine.py.

Coverage:
- VWAP aggregation of partial fills (D-05)
- FIFO entry/exit pairing (D-03)
- Open position exclusion (D-04)
- Per-round-trip fee-adjusted P&L (METR-04 foundation)
- Direction inference for LONG and SHORT round-trips
- Overlapping entries (pyramiding)
- Chronological sort robustness
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from libs.metrics.engine import (
    OrderResult,
    RoundTrip,
    build_round_trips,
    pair_round_trips,
    vwap_aggregate,
)
from libs.storage.repository import AttributedFill

# ---------------------------------------------------------------------------
# Test data factory
# ---------------------------------------------------------------------------

_fill_counter = itertools.count(1)
_trade_counter = itertools.count(1)

_BASE_TS = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _make_fill(
    order_id: str,
    *,
    fill_id: str | None = None,
    instrument: str = "ETH-PERP-INTX",
    side: str = "BUY",
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("100"),
    fee_usdc: Decimal = Decimal("0.50"),
    is_maker: bool = False,
    filled_at: datetime | None = None,
    trade_id: str | None = None,
    primary_source: str = "momentum",
    conviction: float = 0.7,
    portfolio_target: str = "autonomous",
) -> AttributedFill:
    """Create an AttributedFill with sensible defaults. All keyword args overrideable."""
    return AttributedFill(
        fill_id=fill_id or f"fill-{next(_fill_counter)}",
        order_id=order_id,
        portfolio_target=portfolio_target,
        instrument=instrument,
        side=side,
        size=size,
        price=price,
        fee_usdc=fee_usdc,
        is_maker=is_maker,
        filled_at=filled_at or _BASE_TS,
        trade_id=trade_id or f"trade-{next(_trade_counter)}",
        primary_source=primary_source,
        conviction=conviction,
    )


def _make_order(
    order_id: str,
    side: str,
    price: Decimal,
    *,
    size: Decimal = Decimal("1.0"),
    fee: Decimal = Decimal("0.50"),
    ts: datetime | None = None,
    instrument: str = "ETH-PERP-INTX",
    source: str = "momentum",
) -> OrderResult:
    """Create an OrderResult directly (bypassing vwap_aggregate) for pairing tests."""
    return OrderResult(
        order_id=order_id,
        instrument=instrument,
        primary_source=source,
        side=side,
        avg_price=price,
        total_size=size,
        total_fee=fee,
        filled_at=ts or _BASE_TS,
    )


# ---------------------------------------------------------------------------
# Tests: VWAP aggregation
# ---------------------------------------------------------------------------


def test_vwap_aggregation() -> None:
    """3 partial fills for same order_id produce VWAP avg_price and summed size/fees."""
    fills = [
        _make_fill("order-1", price=Decimal("100"), size=Decimal("0.5"), fee_usdc=Decimal("0.10")),
        _make_fill("order-1", price=Decimal("102"), size=Decimal("0.3"), fee_usdc=Decimal("0.20")),
        _make_fill("order-1", price=Decimal("98"), size=Decimal("0.2"), fee_usdc=Decimal("0.05")),
    ]
    result = vwap_aggregate(fills)

    # VWAP = (100*0.5 + 102*0.3 + 98*0.2) / (0.5+0.3+0.2) = (50 + 30.6 + 19.6) / 1.0 = 100.2
    assert result.order_id == "order-1"
    assert result.total_size == Decimal("1.0")
    assert result.avg_price == Decimal("100.2")
    assert result.total_fee == Decimal("0.35")
    assert result.side == "BUY"


def test_vwap_single_fill() -> None:
    """A single fill produces an OrderResult with the same price and size."""
    fill = _make_fill("order-2", price=Decimal("200"), size=Decimal("2.5"), fee_usdc=Decimal("1.00"))
    result = vwap_aggregate([fill])

    assert result.order_id == "order-2"
    assert result.avg_price == Decimal("200")
    assert result.total_size == Decimal("2.5")
    assert result.total_fee == Decimal("1.00")


def test_vwap_zero_size_guard() -> None:
    """Fills with size=0 mixed with normal fills do not cause ZeroDivisionError."""
    fills = [
        _make_fill("order-3", price=Decimal("100"), size=Decimal("0"), fee_usdc=Decimal("0")),
        _make_fill("order-3", price=Decimal("105"), size=Decimal("1.0"), fee_usdc=Decimal("0.50")),
    ]
    # Should not raise ZeroDivisionError; zero-size fill is filtered out
    result = vwap_aggregate(fills)
    assert result.avg_price == Decimal("105")
    assert result.total_size == Decimal("1.0")


# ---------------------------------------------------------------------------
# Tests: FIFO pairing -- direction
# ---------------------------------------------------------------------------


def test_pair_round_trips_long() -> None:
    """BUY order then SELL order produces 1 LONG round-trip."""
    orders = [
        _make_order("entry-1", "BUY", Decimal("100"), ts=_BASE_TS),
        _make_order("exit-1", "SELL", Decimal("110"), ts=_BASE_TS + timedelta(hours=1)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    rt = trips[0]
    assert rt.entry_order_id == "entry-1"
    assert rt.exit_order_id == "exit-1"
    assert rt.side == "BUY"
    assert rt.gross_pnl == (Decimal("110") - Decimal("100")) * Decimal("1.0")


def test_pair_round_trips_short() -> None:
    """SELL order then BUY order produces 1 SHORT round-trip.

    This explicitly tests that a BUY fill closes a short position,
    not opens a long. BUY fill closes short when FIFO stack top is SELL.
    """
    orders = [
        _make_order("entry-s", "SELL", Decimal("110"), ts=_BASE_TS),
        _make_order("exit-s", "BUY", Decimal("100"), ts=_BASE_TS + timedelta(hours=1)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    rt = trips[0]
    assert rt.entry_order_id == "entry-s"
    assert rt.exit_order_id == "exit-s"
    assert rt.side == "SELL"
    # SHORT gross_pnl = (entry - exit) * size = (110 - 100) * 1.0 = 10.0
    assert rt.gross_pnl == (Decimal("110") - Decimal("100")) * Decimal("1.0")


def test_pair_round_trips_multiple() -> None:
    """BUY, SELL, BUY, SELL -> 2 round-trips with FIFO order preserved."""
    t = _BASE_TS
    orders = [
        _make_order("buy-1", "BUY", Decimal("100"), ts=t),
        _make_order("sell-1", "SELL", Decimal("110"), ts=t + timedelta(hours=1)),
        _make_order("buy-2", "BUY", Decimal("105"), ts=t + timedelta(hours=2)),
        _make_order("sell-2", "SELL", Decimal("115"), ts=t + timedelta(hours=3)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 2
    assert trips[0].entry_order_id == "buy-1"
    assert trips[0].exit_order_id == "sell-1"
    assert trips[1].entry_order_id == "buy-2"
    assert trips[1].exit_order_id == "sell-2"


def test_pair_overlapping_entries() -> None:
    """BUY(1), BUY(2), SELL(3), SELL(4) -> 2 round-trips via FIFO stacking.

    Verifies pyramiding: first entry paired with first exit,
    second entry paired with second exit (FIFO order preserved).
    """
    t = _BASE_TS
    orders = [
        _make_order("buy-1", "BUY", Decimal("100"), ts=t),
        _make_order("buy-2", "BUY", Decimal("102"), ts=t + timedelta(hours=1)),
        _make_order("sell-3", "SELL", Decimal("110"), ts=t + timedelta(hours=2)),
        _make_order("sell-4", "SELL", Decimal("112"), ts=t + timedelta(hours=3)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 2
    # FIFO: buy-1 paired with sell-3 (first exit), buy-2 paired with sell-4
    assert trips[0].entry_order_id == "buy-1"
    assert trips[0].exit_order_id == "sell-3"
    assert trips[1].entry_order_id == "buy-2"
    assert trips[1].exit_order_id == "sell-4"


def test_open_position_excluded() -> None:
    """BUY, SELL, BUY (no matching SELL) -> 1 round-trip; trailing BUY excluded per D-04."""
    t = _BASE_TS
    orders = [
        _make_order("buy-1", "BUY", Decimal("100"), ts=t),
        _make_order("sell-1", "SELL", Decimal("110"), ts=t + timedelta(hours=1)),
        _make_order("buy-2", "BUY", Decimal("105"), ts=t + timedelta(hours=2)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    assert trips[0].entry_order_id == "buy-1"
    assert trips[0].exit_order_id == "sell-1"


# ---------------------------------------------------------------------------
# Tests: P&L computation
# ---------------------------------------------------------------------------


def test_round_trip_pnl_long() -> None:
    """LONG round-trip: gross_pnl=10, total_fees=1.0, net_pnl=9.0."""
    orders = [
        _make_order("entry-l", "BUY", Decimal("100"), fee=Decimal("0.50")),
        _make_order("exit-l", "SELL", Decimal("110"), fee=Decimal("0.50"),
                    ts=_BASE_TS + timedelta(hours=1)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    rt = trips[0]
    assert rt.gross_pnl == Decimal("10.0")
    assert rt.total_fees == Decimal("1.0")
    assert rt.net_pnl == Decimal("9.0")


def test_round_trip_pnl_short() -> None:
    """SHORT round-trip: entry SELL at 110, exit BUY at 100 -> gross_pnl=10.0."""
    orders = [
        _make_order("entry-sh", "SELL", Decimal("110"), fee=Decimal("0.50")),
        _make_order("exit-sh", "BUY", Decimal("100"), fee=Decimal("0.50"),
                    ts=_BASE_TS + timedelta(hours=1)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    rt = trips[0]
    assert rt.gross_pnl == Decimal("10.0")
    assert rt.total_fees == Decimal("1.0")
    assert rt.net_pnl == Decimal("9.0")


def test_round_trip_zero_pnl() -> None:
    """Zero-gross-P&L trade produces negative net_pnl (fees make breakeven a loss)."""
    orders = [
        _make_order("entry-z", "BUY", Decimal("100"), fee=Decimal("0.50")),
        _make_order("exit-z", "SELL", Decimal("100"), fee=Decimal("0.50"),
                    ts=_BASE_TS + timedelta(hours=1)),
    ]
    trips = pair_round_trips(orders)

    assert len(trips) == 1
    rt = trips[0]
    assert rt.gross_pnl == Decimal("0.0")
    assert rt.net_pnl < Decimal("0")  # fees make breakeven a loss


# ---------------------------------------------------------------------------
# Tests: build_round_trips
# ---------------------------------------------------------------------------


def test_build_round_trips_groups_by_source_instrument() -> None:
    """Fills from (momentum, ETH) and (mean_reversion, BTC) produce 2 keyed groups."""
    t = _BASE_TS
    fills = [
        # momentum / ETH-PERP-INTX: BUY then SELL
        _make_fill("order-eth-1", side="BUY", price=Decimal("100"), instrument="ETH-PERP-INTX",
                   primary_source="momentum", filled_at=t),
        _make_fill("order-eth-2", side="SELL", price=Decimal("110"), instrument="ETH-PERP-INTX",
                   primary_source="momentum", filled_at=t + timedelta(hours=1)),
        # mean_reversion / BTC-PERP-INTX: BUY then SELL
        _make_fill("order-btc-1", side="BUY", price=Decimal("50000"), instrument="BTC-PERP-INTX",
                   primary_source="mean_reversion", filled_at=t),
        _make_fill("order-btc-2", side="SELL", price=Decimal("51000"), instrument="BTC-PERP-INTX",
                   primary_source="mean_reversion", filled_at=t + timedelta(hours=1)),
    ]
    result = build_round_trips(fills)

    assert len(result) == 2
    assert ("momentum", "ETH-PERP-INTX") in result
    assert ("mean_reversion", "BTC-PERP-INTX") in result
    assert len(result[("momentum", "ETH-PERP-INTX")]) == 1
    assert len(result[("mean_reversion", "BTC-PERP-INTX")]) == 1


def test_build_round_trips_empty() -> None:
    """Empty fill list produces empty dict."""
    result = build_round_trips([])
    assert result == {}


def test_build_round_trips_sorts_by_timestamp() -> None:
    """Fills provided in random timestamp order are paired using chronological order."""
    t = _BASE_TS
    # Provide SELL fill before BUY fill (wrong order) -- should still pair correctly
    fills = [
        _make_fill("order-sell", side="SELL", price=Decimal("110"), instrument="ETH-PERP-INTX",
                   primary_source="momentum", filled_at=t + timedelta(hours=1)),
        _make_fill("order-buy", side="BUY", price=Decimal("100"), instrument="ETH-PERP-INTX",
                   primary_source="momentum", filled_at=t),
    ]
    result = build_round_trips(fills)

    # After sorting by timestamp, BUY comes first -> 1 LONG round-trip
    assert ("momentum", "ETH-PERP-INTX") in result
    trips = result[("momentum", "ETH-PERP-INTX")]
    assert len(trips) == 1
    assert trips[0].side == "BUY"  # entry is BUY (LONG)
    assert trips[0].gross_pnl == Decimal("10.0")
