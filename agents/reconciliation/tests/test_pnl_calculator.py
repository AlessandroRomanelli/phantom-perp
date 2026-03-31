"""Tests for P&L calculation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import OrderSide, Route
from libs.common.models.order import Fill

from agents.reconciliation.pnl_calculator import (
    build_pnl_summary,
    compute_fees_from_fills,
    compute_realized_pnl,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _fill(
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("2200"),
    fee: Decimal = Decimal("0.55"),
    is_maker: bool = True,
    filled_at: datetime = T0,
    order_id: str = "ord-1",
) -> Fill:
    return Fill(
        fill_id=f"fill-{order_id}",
        order_id=order_id,
        route=Route.A,
        instrument="ETH-PERP",
        side=side,
        size=size,
        price=price,
        fee_usdc=fee,
        is_maker=is_maker,
        filled_at=filled_at,
        trade_id=f"trade-{order_id}",
    )


class TestComputeFeesFromFills:
    def test_all_maker(self) -> None:
        fills = [
            _fill(fee=Decimal("0.50"), is_maker=True),
            _fill(fee=Decimal("0.30"), is_maker=True),
        ]
        total, maker, taker = compute_fees_from_fills(fills)
        assert total == Decimal("0.80")
        assert maker == Decimal("0.80")
        assert taker == Decimal("0")

    def test_all_taker(self) -> None:
        fills = [
            _fill(fee=Decimal("1.00"), is_maker=False),
        ]
        total, maker, taker = compute_fees_from_fills(fills)
        assert total == Decimal("1.00")
        assert maker == Decimal("0")
        assert taker == Decimal("1.00")

    def test_mixed(self) -> None:
        fills = [
            _fill(fee=Decimal("0.50"), is_maker=True),
            _fill(fee=Decimal("1.00"), is_maker=False),
        ]
        total, maker, taker = compute_fees_from_fills(fills)
        assert total == Decimal("1.50")
        assert maker == Decimal("0.50")
        assert taker == Decimal("1.00")

    def test_empty_fills(self) -> None:
        total, maker, taker = compute_fees_from_fills([])
        assert total == Decimal("0")
        assert maker == Decimal("0")
        assert taker == Decimal("0")


class TestComputeRealizedPnl:
    def test_simple_profit(self) -> None:
        """Buy at 2200, sell at 2300 → profit = 100 per ETH."""
        fills = [
            _fill(
                side=OrderSide.BUY, size=Decimal("1"), price=Decimal("2200"),
                filled_at=T0, order_id="o1",
            ),
            _fill(
                side=OrderSide.SELL, size=Decimal("1"), price=Decimal("2300"),
                filled_at=T0 + timedelta(hours=1), order_id="o2",
            ),
        ]
        pnl = compute_realized_pnl(fills)
        assert pnl == Decimal("100")

    def test_simple_loss(self) -> None:
        """Buy at 2200, sell at 2100 → loss = -100 per ETH."""
        fills = [
            _fill(
                side=OrderSide.BUY, size=Decimal("1"), price=Decimal("2200"),
                filled_at=T0, order_id="o1",
            ),
            _fill(
                side=OrderSide.SELL, size=Decimal("1"), price=Decimal("2100"),
                filled_at=T0 + timedelta(hours=1), order_id="o2",
            ),
        ]
        pnl = compute_realized_pnl(fills)
        assert pnl == Decimal("-100")

    def test_partial_close(self) -> None:
        """Buy 2 ETH, sell 1 ETH at profit."""
        fills = [
            _fill(
                side=OrderSide.BUY, size=Decimal("2"), price=Decimal("2200"),
                filled_at=T0, order_id="o1",
            ),
            _fill(
                side=OrderSide.SELL, size=Decimal("1"), price=Decimal("2400"),
                filled_at=T0 + timedelta(hours=1), order_id="o2",
            ),
        ]
        pnl = compute_realized_pnl(fills)
        # Realized on 1 ETH: (2400 - 2200) = 200
        assert pnl == Decimal("200")

    def test_average_entry_price(self) -> None:
        """Buy at two prices, sell at one → average entry determines P&L."""
        fills = [
            _fill(
                side=OrderSide.BUY, size=Decimal("1"), price=Decimal("2200"),
                filled_at=T0, order_id="o1",
            ),
            _fill(
                side=OrderSide.BUY, size=Decimal("1"), price=Decimal("2400"),
                filled_at=T0 + timedelta(hours=1), order_id="o2",
            ),
            _fill(
                side=OrderSide.SELL, size=Decimal("2"), price=Decimal("2400"),
                filled_at=T0 + timedelta(hours=2), order_id="o3",
            ),
        ]
        pnl = compute_realized_pnl(fills)
        # Avg entry = (2200 + 2400) / 2 = 2300
        # Realized on 2 ETH: (2400 - 2300) * 2 = 200
        assert pnl == Decimal("200")

    def test_no_sells_no_realized(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("5"), order_id="o1"),
        ]
        pnl = compute_realized_pnl(fills)
        assert pnl == Decimal("0")

    def test_empty_fills(self) -> None:
        assert compute_realized_pnl([]) == Decimal("0")


class TestBuildPnlSummary:
    def test_full_summary(self) -> None:
        fills = [
            _fill(
                side=OrderSide.BUY, size=Decimal("1"), price=Decimal("2200"),
                fee=Decimal("0.55"), is_maker=True, filled_at=T0, order_id="o1",
            ),
            _fill(
                side=OrderSide.SELL, size=Decimal("1"), price=Decimal("2300"),
                fee=Decimal("1.15"), is_maker=False,
                filled_at=T0 + timedelta(hours=1), order_id="o2",
            ),
        ]
        summary = build_pnl_summary(
            fills,
            unrealized_pnl_usdc=Decimal("50"),
            funding_pnl_usdc=Decimal("-5"),
        )
        assert summary.realized_pnl_usdc == Decimal("100")
        assert summary.unrealized_pnl_usdc == Decimal("50")
        assert summary.funding_pnl_usdc == Decimal("-5")
        assert summary.total_fees_usdc == Decimal("1.70")
        assert summary.maker_fees_usdc == Decimal("0.55")
        assert summary.taker_fees_usdc == Decimal("1.15")
        assert summary.fill_count == 2
        # net = 100 + 50 + (-5) - 1.70 = 143.30
        assert summary.net_pnl_usdc == Decimal("143.30")

    def test_maker_ratio(self) -> None:
        fills = [
            _fill(fee=Decimal("0.50"), is_maker=True, order_id="o1"),
            _fill(fee=Decimal("1.50"), is_maker=False, order_id="o2"),
        ]
        summary = build_pnl_summary(fills, Decimal("0"), Decimal("0"))
        # maker = 0.50 / 2.00 = 0.25
        assert summary.maker_ratio == 0.25

    def test_no_fees_maker_ratio_zero(self) -> None:
        summary = build_pnl_summary([], Decimal("0"), Decimal("0"))
        assert summary.maker_ratio == 0.0
