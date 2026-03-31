"""Tests for confirmation agent serialization helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    Route,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, ProposedOrder

from agents.confirmation.main import (
    approved_order_to_dict,
    deserialize_approved_order,
    deserialize_order,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestDeserializeOrder:
    def test_roundtrip_with_risk_agent_format(self) -> None:
        """Deserialize a payload in the exact format produced by risk agent's order_to_dict."""
        payload = {
            "order_id": "ord-123",
            "signal_id": "sig-456",
            "instrument": "ETH-PERP",
            "route": "user_confirmed",
            "side": "BUY",
            "size": "2.5",
            "order_type": "LIMIT",
            "conviction": 0.78,
            "sources": "momentum,sentiment",
            "estimated_margin_required_usdc": "1743.00",
            "estimated_liquidation_price": "1891.00",
            "estimated_fee_usdc": "0.70",
            "estimated_funding_cost_1h_usdc": "-0.08",
            "proposed_at": "2025-06-15T12:00:00+00:00",
            "limit_price": "2232.50",
            "stop_loss": "2188.00",
            "take_profit": "2320.00",
            "leverage": "3.2",
            "reduce_only": "False",
            "status": "risk_approved",
            "reasoning": "Breakout detected",
        }
        order = deserialize_order(payload)
        assert order.order_id == "ord-123"
        assert order.route == Route.B
        assert order.side == OrderSide.BUY
        assert order.size == Decimal("2.5")
        assert order.order_type == OrderType.LIMIT
        assert order.conviction == 0.78
        assert order.sources == [SignalSource.MOMENTUM, SignalSource.SENTIMENT]
        assert order.estimated_margin_required_usdc == Decimal("1743.00")
        assert order.estimated_liquidation_price == Decimal("1891.00")
        assert order.estimated_fee_usdc == Decimal("0.70")
        assert order.estimated_funding_cost_1h_usdc == Decimal("-0.08")
        assert order.limit_price == Decimal("2232.50")
        assert order.stop_loss == Decimal("2188.00")
        assert order.take_profit == Decimal("2320.00")
        assert order.leverage == Decimal("3.2")
        assert order.reduce_only is False
        assert order.status == OrderStatus.RISK_APPROVED
        assert order.reasoning == "Breakout detected"

    def test_none_optional_prices(self) -> None:
        """Risk agent serializes None prices as empty strings."""
        payload = {
            "order_id": "ord-789",
            "signal_id": "sig-789",
            "instrument": "ETH-PERP",
            "route": "user_confirmed",
            "side": "SELL",
            "size": "0.5",
            "order_type": "MARKET",
            "conviction": 0.6,
            "sources": "funding_arb",
            "estimated_margin_required_usdc": "500",
            "estimated_liquidation_price": "2500",
            "estimated_fee_usdc": "0.25",
            "estimated_funding_cost_1h_usdc": "0.10",
            "proposed_at": "2025-06-15T12:00:00+00:00",
            "limit_price": "",
            "stop_loss": "",
            "take_profit": "",
            "leverage": "2",
            "reduce_only": "True",
            "status": "risk_approved",
            "reasoning": "Elevated funding",
        }
        order = deserialize_order(payload)
        assert order.limit_price is None
        assert order.stop_loss is None
        assert order.take_profit is None
        assert order.reduce_only is True
        assert order.side == OrderSide.SELL
        assert order.order_type == OrderType.MARKET

    def test_matches_risk_agent_order_to_dict(self) -> None:
        """Verify we can deserialize what risk agent's order_to_dict produces."""
        from agents.risk.main import order_to_dict

        original = ProposedOrder(
            order_id="ord-rt",
            signal_id="sig-rt",
            instrument="ETH-PERP",
            route=Route.B,
            side=OrderSide.BUY,
            size=Decimal("1.5"),
            order_type=OrderType.LIMIT,
            conviction=0.82,
            sources=[SignalSource.MOMENTUM, SignalSource.CORRELATION],
            estimated_margin_required_usdc=Decimal("900"),
            estimated_liquidation_price=Decimal("1850"),
            estimated_fee_usdc=Decimal("0.60"),
            estimated_funding_cost_1h_usdc=Decimal("-0.05"),
            proposed_at=T0,
            limit_price=Decimal("2200"),
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
            leverage=Decimal("3"),
            reduce_only=False,
            status=OrderStatus.RISK_APPROVED,
            reasoning="Test roundtrip",
        )
        serialized = order_to_dict(original)
        reconstructed = deserialize_order(serialized)

        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == original.route
        assert reconstructed.side == original.side
        assert reconstructed.size == original.size
        assert reconstructed.conviction == original.conviction
        assert reconstructed.sources == original.sources
        assert reconstructed.limit_price == original.limit_price
        assert reconstructed.stop_loss == original.stop_loss
        assert reconstructed.take_profit == original.take_profit
        assert reconstructed.leverage == original.leverage
        assert reconstructed.reduce_only == original.reduce_only


class TestApprovedOrderSerialization:
    def test_roundtrip(self) -> None:
        approved = ApprovedOrder(
            order_id="ord-approved",
            route=Route.B,
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.5"),
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2200"),
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
            leverage=Decimal("3"),
            reduce_only=False,
            approved_at=T0,
        )
        serialized = approved_order_to_dict(approved)
        reconstructed = deserialize_approved_order(serialized)

        assert reconstructed.order_id == "ord-approved"
        assert reconstructed.route == Route.B
        assert reconstructed.side == OrderSide.BUY
        assert reconstructed.size == Decimal("1.5")
        assert reconstructed.limit_price == Decimal("2200")
        assert reconstructed.stop_loss == Decimal("2100")
        assert reconstructed.take_profit == Decimal("2400")
        assert reconstructed.leverage == Decimal("3")
        assert reconstructed.reduce_only is False
        assert reconstructed.approved_at == T0

    def test_none_prices(self) -> None:
        approved = ApprovedOrder(
            order_id="ord-no-prices",
            route=Route.B,
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_loss=None,
            take_profit=None,
            leverage=Decimal("2"),
            reduce_only=True,
            approved_at=T0,
        )
        serialized = approved_order_to_dict(approved)
        reconstructed = deserialize_approved_order(serialized)

        assert reconstructed.limit_price is None
        assert reconstructed.stop_loss is None
        assert reconstructed.take_profit is None
        assert reconstructed.reduce_only is True
