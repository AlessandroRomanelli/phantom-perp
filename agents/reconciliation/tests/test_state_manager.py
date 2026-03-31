"""Tests for portfolio state management."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.coinbase.models import Amount, PortfolioResponse, PositionResponse
from libs.common.models.enums import Route, PositionSide

from agents.reconciliation.state_manager import (
    build_portfolio_snapshot,
    build_position,
    build_system_snapshot,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _amount(value: Decimal | str, currency: str = "USD") -> Amount:
    return Amount(value=str(value), currency=currency)


def _position_resp(
    side: str = "LONG",
    net_size: Decimal = Decimal("2.5"),
    entry: Decimal = Decimal("2200"),
    mark: Decimal = Decimal("2250"),
    unrealized: Decimal = Decimal("125"),
    liq: Decimal | None = Decimal("1890"),
    im_contribution: str = "1100",
) -> PositionResponse:
    return PositionResponse(
        product_id="ETH-PERP",
        portfolio_uuid="test-portfolio-id",
        position_side=side,
        net_size=str(net_size),
        entry_vwap=_amount(entry),
        mark_price=_amount(mark),
        unrealized_pnl=_amount(unrealized),
        liquidation_price=_amount(liq) if liq is not None else None,
        im_contribution=im_contribution,
    )


def _portfolio_resp(
    equity: Decimal = Decimal("10000"),
    used: Decimal = Decimal("3000"),
    available: Decimal = Decimal("7000"),
    unrealized: Decimal = Decimal("125"),
) -> PortfolioResponse:
    return PortfolioResponse(
        portfolio_uuid="test-portfolio-id",
        collateral=str(equity),
        total_balance=_amount(equity),
        portfolio_initial_margin=str(used),
        unrealized_pnl=_amount(unrealized),
    )


class TestBuildPosition:
    def test_long_position(self) -> None:
        pos = build_position(_position_resp(), Route.A)
        assert pos.side == PositionSide.LONG
        assert pos.size == Decimal("2.5")
        assert pos.entry_price == Decimal("2200")
        assert pos.mark_price == Decimal("2250")
        assert pos.unrealized_pnl_usdc == Decimal("125")
        assert pos.route == Route.A
        assert pos.is_open is True

    def test_short_position(self) -> None:
        pos = build_position(
            _position_resp(side="SHORT", net_size=Decimal("-1.0")),
            Route.B,
        )
        assert pos.side == PositionSide.SHORT
        assert pos.size == Decimal("1.0")

    def test_flat_position(self) -> None:
        pos = build_position(
            _position_resp(side="LONG", net_size=Decimal("0")),
            Route.A,
        )
        assert pos.side == PositionSide.FLAT
        assert pos.is_open is False

    def test_leverage_computed(self) -> None:
        # notional = 2.5 * 2250 = 5625, im_contribution = 1100
        # leverage = 5625 / 1100 ≈ 5.11
        pos = build_position(_position_resp(), Route.A)
        assert pos.leverage > Decimal("5")
        assert pos.leverage < Decimal("6")

    def test_liquidation_price_none_becomes_zero(self) -> None:
        pos = build_position(_position_resp(liq=None), Route.A)
        assert pos.liquidation_price == Decimal("0")

    def test_margin_ratio(self) -> None:
        # With Advanced API, margin_ratio is approximated as 0.5 when im > 0
        pos = build_position(_position_resp(), Route.A)
        assert pos.margin_ratio == 0.5


class TestBuildPortfolioSnapshot:
    def test_basic_snapshot(self) -> None:
        snap = build_portfolio_snapshot(
            _portfolio_resp(),
            [_position_resp()],
            Route.A,
            now=T0,
        )
        assert snap.route == Route.A
        # equity = total_balance(10000) + unrealized_pnl(125) = 10125
        assert snap.equity_usdc == Decimal("10125")
        assert snap.used_margin_usdc == Decimal("3000")
        assert snap.available_margin_usdc == Decimal("7000")
        assert len(snap.positions) == 1
        assert snap.timestamp == T0

    def test_margin_utilization(self) -> None:
        # equity = 10000 + 125 = 10125, used=3000 → 3000/10125*100 ≈ 29.63%
        snap = build_portfolio_snapshot(
            _portfolio_resp(),
            [],
            Route.A,
            now=T0,
        )
        assert abs(snap.margin_utilization_pct - 29.63) < 0.01

    def test_zero_equity_margin_util(self) -> None:
        # equity=0 + unrealized(125) = 125, used=3000
        # But with total_balance=0, available=0, used=3000 → equity=0+125=125 > 0
        # So margin_util = 3000/125*100 = 2400% → capped at whatever the model returns
        # Better: pass unrealized=0 so equity stays at 0
        snap = build_portfolio_snapshot(
            _portfolio_resp(equity=Decimal("0"), unrealized=Decimal("0")),
            [],
            Route.A,
            now=T0,
        )
        assert snap.margin_utilization_pct == 0.0

    def test_pnl_fields(self) -> None:
        snap = build_portfolio_snapshot(
            _portfolio_resp(unrealized=Decimal("200")),
            [],
            Route.A,
            realized_pnl_today_usdc=Decimal("50"),
            funding_pnl_today_usdc=Decimal("-10"),
            fees_paid_today_usdc=Decimal("5"),
            now=T0,
        )
        assert snap.unrealized_pnl_usdc == Decimal("200")
        assert snap.realized_pnl_today_usdc == Decimal("50")
        assert snap.funding_pnl_today_usdc == Decimal("-10")
        assert snap.fees_paid_today_usdc == Decimal("5")
        # net = 50 + 200 + (-10) - 5 = 235
        assert snap.net_pnl_today_usdc == Decimal("235")

    def test_open_positions_filtered(self) -> None:
        positions = [
            _position_resp(net_size=Decimal("1")),
            _position_resp(net_size=Decimal("0")),
        ]
        snap = build_portfolio_snapshot(
            _portfolio_resp(),
            positions,
            Route.A,
            now=T0,
        )
        assert len(snap.positions) == 2
        assert len(snap.open_positions) == 1


class TestBuildSystemSnapshot:
    def test_combines_portfolios(self) -> None:
        # equity = total_balance + unrealized(125 default)
        # snap_a: equity = 5000 + 125 = 5125
        # snap_b: equity = 15000 + 125 = 15125
        snap_a = build_portfolio_snapshot(
            _portfolio_resp(equity=Decimal("5000")),
            [],
            Route.A,
            now=T0,
        )
        snap_b = build_portfolio_snapshot(
            _portfolio_resp(equity=Decimal("15000")),
            [],
            Route.B,
            now=T0,
        )
        system = build_system_snapshot(snap_a, snap_b, now=T0)
        assert system.combined_equity_usdc == Decimal("20250")
        assert system.route_a.equity_usdc == Decimal("5125")
        assert system.route_b.equity_usdc == Decimal("15125")
