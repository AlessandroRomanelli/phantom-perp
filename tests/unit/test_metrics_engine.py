"""Unit tests for libs/metrics/engine.py.

Coverage:
- VWAP aggregation of partial fills (D-05)
- FIFO entry/exit pairing (D-03)
- Open position exclusion (D-04)
- Per-round-trip fee-adjusted P&L (METR-04 foundation)
- Direction inference for LONG and SHORT round-trips
- Overlapping entries (pyramiding)
- Chronological sort robustness
- Expectancy (METR-01) -- basic, all-wins, all-losses
- Profit factor (METR-02) -- basic, no-losses guard
- Drawdown (METR-03) -- amount, duration, still-in-drawdown, zero-drawdown
- Fee-adjusted P&L and funding placeholder (METR-04/D-08/D-09)
- Min-count gate (D-01/D-02)
- Multiple strategy/instrument pairs
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from freezegun import freeze_time

from libs.metrics.engine import (
    OrderResult,
    RoundTrip,
    StrategyMetrics,
    build_round_trips,
    compute_strategy_metrics,
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
    route: str = "autonomous",
) -> AttributedFill:
    """Create an AttributedFill with sensible defaults. All keyword args overrideable."""
    return AttributedFill(
        fill_id=fill_id or f"fill-{next(_fill_counter)}",
        order_id=order_id,
        portfolio_target=route,
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


# ---------------------------------------------------------------------------
# Factory for RoundTrip objects (used in compute_strategy_metrics tests)
# ---------------------------------------------------------------------------

_rt_counter = itertools.count(1)


def _make_round_trip(
    *,
    entry_order_id: str | None = None,
    exit_order_id: str | None = None,
    instrument: str = "ETH-PERP-INTX",
    primary_source: str = "momentum",
    side: str = "BUY",
    entry_price: Decimal = Decimal("100"),
    exit_price: Decimal = Decimal("110"),
    size: Decimal = Decimal("1.0"),
    gross_pnl: Decimal = Decimal("10"),
    total_fees: Decimal = Decimal("1.0"),
    net_pnl: Decimal = Decimal("9.0"),
    opened_at: datetime = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    closed_at: datetime = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
) -> RoundTrip:
    """Create a RoundTrip with sensible defaults. All keyword args overrideable."""
    n = next(_rt_counter)
    return RoundTrip(
        entry_order_id=entry_order_id or f"entry-{n}",
        exit_order_id=exit_order_id or f"exit-{n}",
        instrument=instrument,
        primary_source=primary_source,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        size=size,
        gross_pnl=gross_pnl,
        total_fees=total_fees,
        net_pnl=net_pnl,
        opened_at=opened_at,
        closed_at=closed_at,
    )


# ---------------------------------------------------------------------------
# Helpers for min-count gate tests: build fills that produce N complete round-trips
# ---------------------------------------------------------------------------

def _make_n_round_trip_fills(
    n: int,
    *,
    primary_source: str = "momentum",
    instrument: str = "ETH-PERP-INTX",
) -> list[AttributedFill]:
    """Create 2*n fills (n BUY + n SELL pairs) producing exactly n round-trips."""
    fills = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        fills.append(_make_fill(
            f"buy-order-{primary_source}-{i}",
            side="BUY",
            price=Decimal("100"),
            primary_source=primary_source,
            instrument=instrument,
            filled_at=base + timedelta(hours=i * 2),
        ))
        fills.append(_make_fill(
            f"sell-order-{primary_source}-{i}",
            side="SELL",
            price=Decimal("110"),
            primary_source=primary_source,
            instrument=instrument,
            filled_at=base + timedelta(hours=i * 2 + 1),
        ))
    return fills


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- expectancy (METR-01)
# ---------------------------------------------------------------------------


def test_expectancy_basic() -> None:
    """10 round-trips (6 wins, 4 losses) produce expectancy = 3.0."""
    # Wins: net_pnl = [5, 10, 3, 8, 12, 7], avg_win = 7.5, win_rate = 0.6
    # Losses: net_pnl = [-4, -6, -3, -2], avg_loss = 3.75, loss_rate = 0.4
    # expectancy = 7.5 * 0.6 - 3.75 * 0.4 = 4.5 - 1.5 = 3.0
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    win_pnls = [Decimal("5"), Decimal("10"), Decimal("3"), Decimal("8"), Decimal("12"), Decimal("7")]
    loss_pnls = [Decimal("-4"), Decimal("-6"), Decimal("-3"), Decimal("-2")]

    round_trips: list[RoundTrip] = []
    for i, pnl in enumerate(win_pnls):
        round_trips.append(_make_round_trip(
            gross_pnl=pnl + Decimal("1"),  # gross is slightly higher (net = gross - fee)
            total_fees=Decimal("1"),
            net_pnl=pnl,
            opened_at=base + timedelta(hours=i * 2),
            closed_at=base + timedelta(hours=i * 2 + 1),
        ))
    for i, pnl in enumerate(loss_pnls):
        round_trips.append(_make_round_trip(
            gross_pnl=pnl + Decimal("1"),
            total_fees=Decimal("1"),
            net_pnl=pnl,
            opened_at=base + timedelta(hours=(len(win_pnls) + i) * 2),
            closed_at=base + timedelta(hours=(len(win_pnls) + i) * 2 + 1),
        ))

    # Use compute_strategy_metrics via fills that produce these round-trips
    # Easier to test _compute_metrics directly; instead, use a paired-fill approach
    fills = _make_n_round_trip_fills(10)  # won't test exact metrics here; test via round-trips
    # Build fills with specific net_pnl values would require controlling prices
    # Use exact arithmetic approach: build the round-trips we need via exact fills
    base_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    all_pnls = win_pnls + loss_pnls  # 10 trades
    for i, pnl in enumerate(all_pnls):
        # gross_pnl = pnl + fee (fee = 0.50 entry + 0.50 exit = 1.0)
        # For BUY/SELL round-trip: gross = exit_price - entry_price
        # entry=100, size=1 -> exit = 100 + (pnl + 1.0) = 101 + pnl
        entry_price = Decimal("100")
        exit_price = entry_price + pnl + Decimal("1")  # pnl = gross - fee; gross = exit-entry
        test_fills.append(_make_fill(
            f"exp-buy-{i}",
            side="BUY",
            price=entry_price,
            fee_usdc=Decimal("0.50"),
            primary_source="test_expectancy",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"exp-sell-{i}",
            side="SELL",
            price=exit_price,
            fee_usdc=Decimal("0.50"),
            primary_source="test_expectancy",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("test_expectancy", "TEST-PERP")
    assert key in result
    metrics = result[key]
    assert metrics is not None
    assert metrics.win_count == 6
    assert metrics.loss_count == 4
    assert abs(float(metrics.expectancy_usdc) - 3.0) < 0.001


def test_expectancy_all_wins() -> None:
    """10 winning round-trips produce positive expectancy with loss_rate=0."""
    base_ts = datetime(2026, 2, 2, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    for i in range(10):
        test_fills.append(_make_fill(
            f"allwin-buy-{i}",
            side="BUY",
            price=Decimal("100"),
            fee_usdc=Decimal("0.50"),
            primary_source="allwins",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"allwin-sell-{i}",
            side="SELL",
            price=Decimal("110"),  # always profitable
            fee_usdc=Decimal("0.50"),
            primary_source="allwins",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("allwins", "TEST-PERP")
    assert key in result
    metrics = result[key]
    assert metrics is not None
    assert metrics.win_count == 10
    assert metrics.loss_count == 0
    assert metrics.expectancy_usdc > Decimal("0")
    # expectancy = avg_win * win_rate - avg_loss * loss_rate = avg_win * 1.0 - 0 * 0.0
    # avg_win = net_pnl = 10 - 1 = 9.0; expectancy = 9.0
    assert abs(float(metrics.expectancy_usdc) - 9.0) < 0.001


def test_expectancy_all_losses() -> None:
    """10 losing round-trips produce negative expectancy with win_rate=0."""
    base_ts = datetime(2026, 2, 3, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    for i in range(10):
        test_fills.append(_make_fill(
            f"allloss-buy-{i}",
            side="BUY",
            price=Decimal("110"),
            fee_usdc=Decimal("0.50"),
            primary_source="alllosses",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"allloss-sell-{i}",
            side="SELL",
            price=Decimal("100"),  # always a loss
            fee_usdc=Decimal("0.50"),
            primary_source="alllosses",
            instrument="TEST-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("alllosses", "TEST-PERP")
    assert key in result
    metrics = result[key]
    assert metrics is not None
    assert metrics.win_count == 0
    assert metrics.loss_count == 10
    assert metrics.expectancy_usdc < Decimal("0")


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- min-count gate (D-01/D-02)
# ---------------------------------------------------------------------------


def test_min_count_gate_returns_none() -> None:
    """9 round-trips for a pair returns None for that key."""
    fills = _make_n_round_trip_fills(9, primary_source="gated", instrument="GATE-PERP")
    result = compute_strategy_metrics(fills, min_trades=10)
    assert ("gated", "GATE-PERP") in result
    assert result[("gated", "GATE-PERP")] is None


def test_min_count_gate_boundary() -> None:
    """Exactly 10 round-trips returns StrategyMetrics (not None)."""
    fills = _make_n_round_trip_fills(10, primary_source="boundary", instrument="BOUND-PERP")
    result = compute_strategy_metrics(fills, min_trades=10)
    assert ("boundary", "BOUND-PERP") in result
    metrics = result[("boundary", "BOUND-PERP")]
    assert metrics is not None
    assert isinstance(metrics, StrategyMetrics)
    assert metrics.trade_count == 10


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- profit factor (METR-02)
# ---------------------------------------------------------------------------


def test_profit_factor_basic() -> None:
    """gross_wins=[10, 20, 15], gross_losses=[-5, -10] -> profit_factor = 45/15 = 3.0."""
    # gross_profit = 10+20+15 = 45; gross_loss = 5+10 = 15; profit_factor = 3.0
    # BUY entry at 100, SELL exit at (100 + gross_pnl) for wins; at (100 - |gross_loss|) for losses
    # We need exactly 10 round-trips total; pad with 5 small wins
    base_ts = datetime(2026, 2, 4, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []

    # 3 big wins: gross = 10, 20, 15
    win_gross = [Decimal("10"), Decimal("20"), Decimal("15")]
    loss_gross = [Decimal("-5"), Decimal("-10")]
    # 5 small wins: gross = 1 each (to reach 10 total)
    small_wins = [Decimal("1")] * 5

    all_entries = list(enumerate(win_gross + loss_gross + small_wins))
    for i, gross in all_entries:
        entry_price = Decimal("100")
        exit_price = entry_price + gross
        test_fills.append(_make_fill(
            f"pf-buy-{i}",
            side="BUY",
            price=entry_price,
            fee_usdc=Decimal("0"),  # zero fees for clean gross = net comparison
            primary_source="pf_test",
            instrument="PF-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"pf-sell-{i}",
            side="SELL",
            price=exit_price,
            fee_usdc=Decimal("0"),
            primary_source="pf_test",
            instrument="PF-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("pf_test", "PF-PERP")
    assert key in result
    metrics = result[key]
    assert metrics is not None
    assert metrics.profit_factor is not None
    # gross_profit = 10+20+15+1+1+1+1+1 = 50; gross_loss = 5+10 = 15; profit_factor = 50/15
    assert abs(metrics.profit_factor - 50.0 / 15.0) < 0.001


def test_profit_factor_no_losses() -> None:
    """All winning round-trips produce profit_factor = None (zero gross_loss guard)."""
    base_ts = datetime(2026, 2, 5, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    for i in range(10):
        test_fills.append(_make_fill(
            f"noloss-buy-{i}",
            side="BUY",
            price=Decimal("100"),
            fee_usdc=Decimal("0"),
            primary_source="noloss",
            instrument="NL-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"noloss-sell-{i}",
            side="SELL",
            price=Decimal("115"),  # always profitable
            fee_usdc=Decimal("0"),
            primary_source="noloss",
            instrument="NL-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("noloss", "NL-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.profit_factor is None  # no losing trades -> None (not division by zero)


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- max drawdown (METR-03)
# ---------------------------------------------------------------------------


def test_max_drawdown_amount() -> None:
    """Cumulative net P&L curve [5,15,10,8,20] -> peak=15, trough=8, max_dd=7."""
    # Cumulative sequence via net_pnls: [5, 10, -5, -2, 12]
    # sum([5]) = 5, sum([5,10]) = 15 (peak), sum([5,10,-5]) = 10, sum([..,-2]) = 8 (trough), sum([..,12]) = 20
    # max_dd = 15 - 8 = 7
    net_pnls = [Decimal("5"), Decimal("10"), Decimal("-5"), Decimal("-2"), Decimal("12")]
    # Pad to 10 trades: add 5 more winning trades before to hit min_trades
    # But we need them BEFORE to not affect the peak/trough sequence
    # Solution: add 5 zero-net-pnl trades at the start (not touching drawdown)
    # Actually just add 5 winning $0 net trades at the start with positive cumulative
    # Easier: add them AFTER the sequence ends (post-recovery)
    base_ts = datetime(2026, 2, 6, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []

    all_pnls = net_pnls + [Decimal("1")] * 5  # 10 total trades
    for i, net_pnl in enumerate(all_pnls):
        entry_price = Decimal("100")
        # gross = net (zero fees for clean calculation)
        exit_price = entry_price + net_pnl
        test_fills.append(_make_fill(
            f"dd-buy-{i}",
            side="BUY",
            price=entry_price,
            fee_usdc=Decimal("0"),
            primary_source="dd_test",
            instrument="DD-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"dd-sell-{i}",
            side="SELL",
            price=exit_price,
            fee_usdc=Decimal("0"),
            primary_source="dd_test",
            instrument="DD-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("dd_test", "DD-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.max_drawdown_usdc == Decimal("7")


def test_drawdown_duration_hours() -> None:
    """Peak at T+0h, trough at T+48h -> drawdown duration = 48.0 hours."""
    base_ts = datetime(2026, 2, 7, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []

    # Sequence: win at T=0, loss at T=48h, then 8 more wins to reach min 10
    # Peak at T=1h (after first win closes), trough at T=49h (after loss closes)
    # duration = 49h - 1h = 48h
    trades = [
        # (net_pnl, close_time_hours)
        (Decimal("10"), 1),    # peak set at T=1h
        (Decimal("-5"), 49),   # trough at T=49h; duration from T=1 to T=49 = 48h
        (Decimal("6"), 97),    # recovery (cumulative = 11)
        (Decimal("1"), 100),
        (Decimal("1"), 101),
        (Decimal("1"), 102),
        (Decimal("1"), 103),
        (Decimal("1"), 104),
        (Decimal("1"), 105),
        (Decimal("1"), 106),
    ]

    for i, (pnl, close_h) in enumerate(trades):
        entry_price = Decimal("100")
        exit_price = entry_price + pnl
        open_ts = base_ts + timedelta(hours=close_h - 1)
        close_ts = base_ts + timedelta(hours=close_h)
        test_fills.append(_make_fill(
            f"dur-buy-{i}",
            side="BUY",
            price=entry_price,
            fee_usdc=Decimal("0"),
            primary_source="dur_test",
            instrument="DUR-PERP",
            filled_at=open_ts,
        ))
        test_fills.append(_make_fill(
            f"dur-sell-{i}",
            side="SELL",
            price=exit_price,
            fee_usdc=Decimal("0"),
            primary_source="dur_test",
            instrument="DUR-PERP",
            filled_at=close_ts,
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("dur_test", "DUR-PERP")
    metrics = result[key]
    assert metrics is not None
    # Duration from peak (T=1h) to trough close (T=49h) = 48h
    assert abs(metrics.max_drawdown_duration_hours - 48.0) < 0.1


@freeze_time("2026-01-05 00:00:00")
def test_drawdown_duration_still_in_drawdown() -> None:
    """Still-in-drawdown: peak at T+1h, no recovery -> duration uses frozen current time."""
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []

    # Peak at T=1h (after first trade closes), then 9 losses (never recover)
    trades = [
        (Decimal("10"), 1),   # peak; cumulative = 10
        (Decimal("-2"), 25),  # cumulative = 8
        (Decimal("-2"), 49),
        (Decimal("-2"), 73),
        (Decimal("-2"), 97),
        (Decimal("-2"), 121),
        (Decimal("-1"), 145),
        (Decimal("-1"), 169),
        (Decimal("-1"), 193),
        (Decimal("-1"), 217),  # cumulative = 10 - 10*2 - 4 = still negative? Let's check:
        # 10 - 2-2-2-2-2-1-1-1-1 = 10 - 14 = -4, well below peak
    ]

    for i, (pnl, close_h) in enumerate(trades):
        entry_price = Decimal("100")
        exit_price = entry_price + pnl
        open_ts = base_ts + timedelta(hours=close_h - 1)
        close_ts = base_ts + timedelta(hours=close_h)
        test_fills.append(_make_fill(
            f"sdd-buy-{i}",
            side="BUY",
            price=entry_price,
            fee_usdc=Decimal("0"),
            primary_source="still_dd",
            instrument="SDD-PERP",
            filled_at=open_ts,
        ))
        test_fills.append(_make_fill(
            f"sdd-sell-{i}",
            side="SELL",
            price=exit_price,
            fee_usdc=Decimal("0"),
            primary_source="still_dd",
            instrument="SDD-PERP",
            filled_at=close_ts,
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("still_dd", "SDD-PERP")
    metrics = result[key]
    assert metrics is not None
    # frozen time = 2026-01-05 00:00:00 UTC = base_ts + 96h
    # peak_time = base_ts + 1h; frozen_now = base_ts + 96h
    # still_in_dd_duration = (96 - 1) = 95 hours
    # The last trade's drawdown from peak = 10 - (-4) = 14 at T=217h
    # still-in-drawdown duration = now - peak_time = (2026-01-05 - 2026-01-01T01:00) ~95h
    # Duration should be at least 95 hours (from peak at T=1h to frozen now = 96h)
    assert metrics.max_drawdown_duration_hours >= 94.0  # ~95h


def test_drawdown_zero_drawdown() -> None:
    """All 10 round-trips positive -> max_drawdown_usdc = 0, duration = 0.0."""
    base_ts = datetime(2026, 2, 8, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    for i in range(10):
        test_fills.append(_make_fill(
            f"zdd-buy-{i}",
            side="BUY",
            price=Decimal("100"),
            fee_usdc=Decimal("0"),
            primary_source="zero_dd",
            instrument="ZDD-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"zdd-sell-{i}",
            side="SELL",
            price=Decimal("110"),  # always +10 net (zero fees)
            fee_usdc=Decimal("0"),
            primary_source="zero_dd",
            instrument="ZDD-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("zero_dd", "ZDD-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.max_drawdown_usdc == Decimal("0")
    assert metrics.max_drawdown_duration_hours == 0.0


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- fee adjustment and P&L reporting (METR-04)
# ---------------------------------------------------------------------------


def test_fee_adjustment() -> None:
    """total_fees_usdc in StrategyMetrics equals sum of all round-trip fees."""
    base_ts = datetime(2026, 2, 9, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    # 10 trades, each with entry fee=0.50 + exit fee=0.50 = 1.0 per round-trip
    # total_fees = 10 * 1.0 = 10.0
    for i in range(10):
        test_fills.append(_make_fill(
            f"fee-buy-{i}",
            side="BUY",
            price=Decimal("100"),
            fee_usdc=Decimal("0.50"),
            primary_source="fee_test",
            instrument="FEE-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"fee-sell-{i}",
            side="SELL",
            price=Decimal("115"),
            fee_usdc=Decimal("0.50"),
            primary_source="fee_test",
            instrument="FEE-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("fee_test", "FEE-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.total_fees_usdc == Decimal("10.0")  # 10 trades * 1.0 per trade


def test_gross_and_net_pnl_reported() -> None:
    """StrategyMetrics has total_gross_pnl and total_net_pnl; net = gross - fees - funding."""
    base_ts = datetime(2026, 2, 10, tzinfo=timezone.utc)
    test_fills: list[AttributedFill] = []
    # 10 trades: gross = 15 each, fee = 1.0 each -> net = 14 each
    for i in range(10):
        test_fills.append(_make_fill(
            f"gnp-buy-{i}",
            side="BUY",
            price=Decimal("100"),
            fee_usdc=Decimal("0.50"),
            primary_source="gnp_test",
            instrument="GNP-PERP",
            filled_at=base_ts + timedelta(hours=i * 2),
        ))
        test_fills.append(_make_fill(
            f"gnp-sell-{i}",
            side="SELL",
            price=Decimal("115"),
            fee_usdc=Decimal("0.50"),
            primary_source="gnp_test",
            instrument="GNP-PERP",
            filled_at=base_ts + timedelta(hours=i * 2 + 1),
        ))

    result = compute_strategy_metrics(test_fills, min_trades=10)
    key = ("gnp_test", "GNP-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.total_gross_pnl == Decimal("150")  # 10 * 15
    assert metrics.total_fees_usdc == Decimal("10.0")  # 10 * 1.0
    assert metrics.funding_costs_usdc == Decimal("0")
    assert metrics.total_net_pnl == metrics.total_gross_pnl - metrics.total_fees_usdc - metrics.funding_costs_usdc
    assert metrics.total_net_pnl == Decimal("140")


def test_funding_costs_placeholder() -> None:
    """StrategyMetrics.funding_costs_usdc == Decimal('0') per D-08 placeholder."""
    fills = _make_n_round_trip_fills(10, primary_source="funding_ph", instrument="FP-PERP")
    result = compute_strategy_metrics(fills, min_trades=10)
    key = ("funding_ph", "FP-PERP")
    metrics = result[key]
    assert metrics is not None
    assert metrics.funding_costs_usdc == Decimal("0")


# ---------------------------------------------------------------------------
# Tests: compute_strategy_metrics -- multiple pairs and empty input
# ---------------------------------------------------------------------------


def test_multiple_strategy_instrument_pairs() -> None:
    """2 strategies x 2 instruments produce dict with 4 keys, each with metrics or None."""
    fills: list[AttributedFill] = []
    # strategy1 / instrument1: 10 trades -> StrategyMetrics
    fills += _make_n_round_trip_fills(10, primary_source="strat1", instrument="INS1-PERP")
    # strategy1 / instrument2: 10 trades -> StrategyMetrics
    fills += _make_n_round_trip_fills(10, primary_source="strat1", instrument="INS2-PERP")
    # strategy2 / instrument1: 10 trades -> StrategyMetrics
    fills += _make_n_round_trip_fills(10, primary_source="strat2", instrument="INS1-PERP")
    # strategy2 / instrument2: 9 trades -> None (below min_trades)
    fills += _make_n_round_trip_fills(9, primary_source="strat2", instrument="INS2-PERP")

    result = compute_strategy_metrics(fills, min_trades=10)
    assert len(result) == 4
    assert result[("strat1", "INS1-PERP")] is not None
    assert result[("strat1", "INS2-PERP")] is not None
    assert result[("strat2", "INS1-PERP")] is not None
    assert result[("strat2", "INS2-PERP")] is None


def test_compute_strategy_metrics_empty() -> None:
    """Empty fill list produces empty dict."""
    result = compute_strategy_metrics([], min_trades=10)
    assert result == {}
