"""Tests for Telegram message composition."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)
from libs.common.models.order import ProposedOrder

from agents.confirmation.message_composer import (
    compose_batch_header,
    compose_expiry_notice,
    compose_stale_price_warning,
    compose_trade_request,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _order(
    side: OrderSide = OrderSide.BUY,
    limit_price: Decimal | None = Decimal("2232.50"),
    size: Decimal = Decimal("2.5"),
) -> ProposedOrder:
    return ProposedOrder(
        order_id="ord-test-001",
        signal_id="sig-001",
        instrument="ETH-PERP",
        portfolio_target=PortfolioTarget.B,
        side=side,
        size=size,
        order_type=OrderType.LIMIT,
        conviction=0.78,
        sources=[SignalSource.MOMENTUM, SignalSource.SENTIMENT],
        estimated_margin_required_usdc=Decimal("1743"),
        estimated_liquidation_price=Decimal("1891"),
        estimated_fee_usdc=Decimal("0.70"),
        estimated_funding_cost_1h_usdc=Decimal("-0.08"),
        proposed_at=T0,
        limit_price=limit_price,
        stop_loss=Decimal("2188"),
        take_profit=Decimal("2320"),
        leverage=Decimal("3.2"),
        reduce_only=False,
        status=OrderStatus.RISK_APPROVED,
        reasoning="Breakout above 4h EMA, positive CT sentiment surge",
    )


class TestComposeTradeRequest:
    def test_contains_instrument_and_direction(self) -> None:
        msg = compose_trade_request(_order())
        assert "ETH-PERP" in msg
        assert "LONG" in msg

    def test_short_direction(self) -> None:
        msg = compose_trade_request(_order(side=OrderSide.SELL))
        assert "SHORT" in msg

    def test_contains_size_and_notional(self) -> None:
        msg = compose_trade_request(_order())
        assert "2.5 ETH" in msg
        # notional = 2.5 * 2232.50 = 5581.25
        assert "5,581.25 USDC" in msg

    def test_contains_entry_price(self) -> None:
        msg = compose_trade_request(_order())
        assert "$2,232.50" in msg
        assert "limit" in msg.lower()

    def test_market_order_entry(self) -> None:
        order = _order(limit_price=None)
        msg = compose_trade_request(order)
        assert "market" in msg.lower()

    def test_contains_stop_loss_and_take_profit(self) -> None:
        msg = compose_trade_request(_order())
        assert "$2,188" in msg
        assert "$2,320" in msg

    def test_contains_leverage(self) -> None:
        msg = compose_trade_request(_order())
        assert "3.2x" in msg

    def test_contains_signal_sources(self) -> None:
        msg = compose_trade_request(_order())
        assert "Momentum" in msg
        assert "Sentiment" in msg

    def test_contains_conviction(self) -> None:
        msg = compose_trade_request(_order())
        assert "0.78" in msg

    def test_contains_reasoning(self) -> None:
        msg = compose_trade_request(_order())
        assert "Breakout above 4h EMA" in msg

    def test_contains_margin_required(self) -> None:
        msg = compose_trade_request(_order())
        assert "1,743" in msg

    def test_contains_liquidation_price(self) -> None:
        msg = compose_trade_request(_order())
        assert "$1,891" in msg

    def test_contains_fee_estimate(self) -> None:
        msg = compose_trade_request(_order())
        assert "0.70 USDC" in msg

    def test_contains_funding_info(self) -> None:
        msg = compose_trade_request(_order())
        assert "you receive" in msg

    def test_includes_portfolio_equity_when_provided(self) -> None:
        msg = compose_trade_request(
            _order(),
            portfolio_equity_usdc=Decimal("45230"),
            margin_utilization_pct=38.0,
        )
        assert "45,230" in msg
        assert "38%" in msg

    def test_sequence_number(self) -> None:
        msg = compose_trade_request(_order(), sequence_number=472)
        assert "#0472" in msg


class TestBatchHeader:
    def test_batch_header(self) -> None:
        header = compose_batch_header(3)
        assert "3" in header
        assert "Portfolio B" in header


class TestExpiryNotice:
    def test_contains_key_info(self) -> None:
        msg = compose_expiry_notice(_order())
        assert "Expired" in msg
        assert "ETH-PERP" in msg
        assert "LONG" in msg
        assert "2.5 ETH" in msg


class TestStalePriceWarning:
    def test_contains_price_move(self) -> None:
        msg = compose_stale_price_warning(
            _order(),
            Decimal("2232.50"),
            Decimal("2280.00"),
        )
        assert "2.1" in msg  # ~2.1% move
        assert "$2,232.50" in msg
        assert "$2,280.00" in msg
