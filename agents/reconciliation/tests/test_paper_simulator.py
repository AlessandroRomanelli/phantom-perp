"""Tests for the paper trading simulator."""

from decimal import Decimal

import pytest

from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.models.enums import OrderSide, PortfolioTarget, PositionSide

from agents.reconciliation.paper_simulator import PaperPortfolio, SimulatedPosition


@pytest.fixture
def portfolio_a() -> PaperPortfolio:
    return PaperPortfolio(
        target=PortfolioTarget.A,
        initial_equity=Decimal("10000"),
    )


class TestSimulatedPosition:
    def test_long_unrealized_profit(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.LONG,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("2100")) == Decimal("100")

    def test_long_unrealized_loss(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.LONG,
            size=Decimal("2.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("1950")) == Decimal("-100")

    def test_short_unrealized_profit(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.SHORT,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("1900")) == Decimal("100")

    def test_short_unrealized_loss(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.SHORT,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("2100")) == Decimal("-100")

    def test_flat_no_pnl(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.FLAT,
            size=Decimal("0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("3000")) == Decimal("0")


class TestPaperPortfolioFills:
    def test_open_long_position(self, portfolio_a: PaperPortfolio) -> None:
        fill = portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        assert fill.side == OrderSide.BUY
        assert fill.size == Decimal("1.0")
        assert fill.price == Decimal("2000")
        assert fill.is_maker is True
        assert fill.fee_usdc == (Decimal("2000") * FEE_MAKER).quantize(Decimal("0.01"))

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.LONG
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.entry_price == Decimal("2000")

    def test_open_short_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            fill_price=Decimal("2500"),
            is_maker=False,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.SHORT
        assert portfolio_a.position.size == Decimal("0.5")
        # Taker fee
        expected_fee = (Decimal("1250") * FEE_TAKER).quantize(Decimal("0.01"))
        assert portfolio_a.fees_paid == expected_fee

    def test_add_to_position_averages_entry(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.size == Decimal("2.0")
        assert portfolio_a.position.entry_price == Decimal("2100")  # avg

    def test_close_position_realizes_profit(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        assert portfolio_a.position is None
        assert portfolio_a.realized_pnl == Decimal("100")

    def test_close_position_realizes_loss(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("1900"),
            is_maker=True,
        )

        assert portfolio_a.position is None
        assert portfolio_a.realized_pnl == Decimal("-100")

    def test_partial_close(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("2.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.side == PositionSide.LONG
        assert portfolio_a.realized_pnl == Decimal("200")  # (2200-2000)*1

    def test_flip_position(self, portfolio_a: PaperPortfolio) -> None:
        # Open long 1.0
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        # Sell 2.0 — closes 1.0 long + opens 1.0 short
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("2.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.SHORT
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.entry_price == Decimal("2100")
        assert portfolio_a.realized_pnl == Decimal("100")  # from closing the long

    def test_fill_count_increments(self, portfolio_a: PaperPortfolio) -> None:
        for i in range(3):
            portfolio_a.apply_fill(
                order_id=f"ord-{i}",
                instrument="ETH-PERP",
                side=OrderSide.BUY,
                size=Decimal("0.1"),
                fill_price=Decimal("2000"),
                is_maker=True,
            )
        assert portfolio_a.fill_count == 3


class TestPaperPortfolioFunding:
    def test_no_position_no_funding(self, portfolio_a: PaperPortfolio) -> None:
        result = portfolio_a.apply_funding(Decimal("0.0001"), Decimal("2000"))
        assert result is None

    def test_long_pays_positive_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding(Decimal("0.0001"), Decimal("2000"))

        assert payment is not None
        # rate=0.0001 * notional=2000 = 0.20, long pays → -0.20
        assert payment.payment_usdc == Decimal("-0.20")
        assert payment.position_side == PositionSide.LONG
        assert portfolio_a.funding_pnl == Decimal("-0.20")

    def test_short_receives_positive_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding(Decimal("0.0001"), Decimal("2000"))

        assert payment is not None
        assert payment.payment_usdc == Decimal("0.20")
        assert payment.position_side == PositionSide.SHORT
        assert portfolio_a.funding_pnl == Decimal("0.20")

    def test_long_receives_negative_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding(Decimal("-0.0002"), Decimal("2000"))

        assert payment is not None
        # -rate * notional = -(-0.0002)*2000 = +0.40 for longs
        assert payment.payment_usdc == Decimal("0.40")
        assert portfolio_a.funding_pnl == Decimal("0.40")

    def test_cumulative_funding_on_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        portfolio_a.apply_funding(Decimal("0.0001"), Decimal("2000"))
        portfolio_a.apply_funding(Decimal("0.0001"), Decimal("2000"))

        assert portfolio_a.position is not None
        assert portfolio_a.position.cumulative_funding_usdc == Decimal("-0.40")


class TestPaperPortfolioSnapshot:
    def test_empty_portfolio_snapshot(self, portfolio_a: PaperPortfolio) -> None:
        snapshot = portfolio_a.build_snapshot(Decimal("2000"))

        assert snapshot.equity_usdc == Decimal("10000.00")
        assert snapshot.used_margin_usdc == Decimal("0")
        assert snapshot.unrealized_pnl_usdc == Decimal("0.00")
        assert len(snapshot.positions) == 0
        assert len(snapshot.open_positions) == 0

    def test_snapshot_with_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot(Decimal("2100"))

        assert len(snapshot.open_positions) == 1
        assert snapshot.unrealized_pnl_usdc == Decimal("100.00")
        assert snapshot.used_margin_usdc > Decimal("0")
        assert snapshot.margin_utilization_pct > 0

        pos = snapshot.positions[0]
        assert pos.side == PositionSide.LONG
        assert pos.size == Decimal("1.0")
        assert pos.entry_price == Decimal("2000")
        assert pos.mark_price == Decimal("2100")

    def test_equity_reflects_fees(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot(Decimal("2000"))

        # Equity should be initial - fees (no unrealized P&L at same price)
        expected_fee = (Decimal("2000") * FEE_MAKER).quantize(Decimal("0.01"))
        assert snapshot.fees_paid_today_usdc == expected_fee
        # equity = 10000 - fee + unrealized(0)
        assert snapshot.equity_usdc == (Decimal("10000") - expected_fee).quantize(Decimal("0.01"))

    def test_equity_reflects_realized_pnl(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot(Decimal("2100"))

        assert snapshot.realized_pnl_today_usdc == Decimal("100.00")
        assert snapshot.fees_paid_today_usdc > Decimal("0")
        # No position, so no unrealized P&L
        assert snapshot.unrealized_pnl_usdc == Decimal("0.00")

    def test_snapshot_portfolio_metadata(self, portfolio_a: PaperPortfolio) -> None:
        snapshot = portfolio_a.build_snapshot(Decimal("2000"))

        assert snapshot.portfolio_target == PortfolioTarget.A
