"""Tests for execution agent serialization helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    Route,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder

from agents.execution.main import (
    deserialize_approved_order,
    deserialize_fill,
    deserialize_proposed_order,
    fill_to_dict,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestDeserializeProposedOrder:
    def test_roundtrip_with_risk_agent_format(self) -> None:
        """Verify we can deserialize what risk agent's order_to_dict produces."""
        from agents.risk.main import order_to_dict

        original = ProposedOrder(
            order_id="ord-exec-1",
            signal_id="sig-exec-1",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.BUY,
            size=Decimal("2.5"),
            order_type=OrderType.LIMIT,
            conviction=0.85,
            sources=[SignalSource.MOMENTUM, SignalSource.SENTIMENT],
            estimated_margin_required_usdc=Decimal("1700"),
            estimated_liquidation_price=Decimal("1890"),
            estimated_fee_usdc=Decimal("0.70"),
            estimated_funding_cost_1h_usdc=Decimal("-0.08"),
            proposed_at=T0,
            limit_price=Decimal("2200"),
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
            leverage=Decimal("3"),
            reduce_only=False,
            status=OrderStatus.RISK_APPROVED,
            reasoning="Strong momentum",
        )
        serialized = order_to_dict(original)
        reconstructed = deserialize_proposed_order(serialized)

        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == Route.A
        assert reconstructed.side == original.side
        assert reconstructed.size == original.size
        assert reconstructed.sources == original.sources
        assert reconstructed.limit_price == original.limit_price
        assert reconstructed.stop_loss == original.stop_loss
        assert reconstructed.take_profit == original.take_profit
        assert reconstructed.leverage == original.leverage
        assert reconstructed.reduce_only is False

    def test_none_prices(self) -> None:
        from agents.risk.main import order_to_dict

        original = ProposedOrder(
            order_id="ord-np",
            signal_id="sig-np",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            order_type=OrderType.MARKET,
            conviction=0.6,
            sources=[SignalSource.FUNDING_ARB],
            estimated_margin_required_usdc=Decimal("500"),
            estimated_liquidation_price=Decimal("2500"),
            estimated_fee_usdc=Decimal("0.25"),
            estimated_funding_cost_1h_usdc=Decimal("0.10"),
            proposed_at=T0,
            leverage=Decimal("2"),
            reduce_only=True,
            status=OrderStatus.RISK_APPROVED,
        )
        serialized = order_to_dict(original)
        reconstructed = deserialize_proposed_order(serialized)
        assert reconstructed.limit_price is None
        assert reconstructed.stop_loss is None
        assert reconstructed.take_profit is None
        assert reconstructed.reduce_only is True


class TestDeserializeApprovedOrder:
    def test_roundtrip_with_confirmation_agent_format(self) -> None:
        """Verify we can deserialize what confirmation agent's approved_order_to_dict produces."""
        from agents.confirmation.main import approved_order_to_dict

        original = ApprovedOrder(
            order_id="ord-conf-1",
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
        serialized = approved_order_to_dict(original)
        reconstructed = deserialize_approved_order(serialized)

        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == Route.B
        assert reconstructed.side == original.side
        assert reconstructed.size == original.size
        assert reconstructed.limit_price == original.limit_price
        assert reconstructed.stop_loss == original.stop_loss
        assert reconstructed.take_profit == original.take_profit
        assert reconstructed.leverage == original.leverage
        assert reconstructed.reduce_only is False
        assert reconstructed.approved_at == T0


class TestFillSerialization:
    def test_roundtrip(self) -> None:
        fill = Fill(
            fill_id="fill-001",
            order_id="ord-001",
            route=Route.A,
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("2.5"),
            price=Decimal("2200"),
            fee_usdc=Decimal("0.69"),
            is_maker=True,
            filled_at=T0,
            trade_id="trade-001",
        )
        serialized = fill_to_dict(fill)
        reconstructed = deserialize_fill(serialized)

        assert reconstructed.fill_id == "fill-001"
        assert reconstructed.order_id == "ord-001"
        assert reconstructed.route == Route.A
        assert reconstructed.side == OrderSide.BUY
        assert reconstructed.size == Decimal("2.5")
        assert reconstructed.price == Decimal("2200")
        assert reconstructed.fee_usdc == Decimal("0.69")
        assert reconstructed.is_maker is True
        assert reconstructed.trade_id == "trade-001"

    def test_taker_fill(self) -> None:
        fill = Fill(
            fill_id="fill-002",
            order_id="ord-002",
            route=Route.B,
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            price=Decimal("2250"),
            fee_usdc=Decimal("0.28"),
            is_maker=False,
            filled_at=T0,
            trade_id="trade-002",
        )
        serialized = fill_to_dict(fill)
        reconstructed = deserialize_fill(serialized)

        assert reconstructed.route == Route.B
        assert reconstructed.side == OrderSide.SELL
        assert reconstructed.is_maker is False
