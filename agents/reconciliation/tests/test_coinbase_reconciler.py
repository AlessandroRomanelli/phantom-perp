"""Tests for Coinbase position reconciliation."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.coinbase.models import Amount, OrderResponse, PositionResponse
from libs.common.models.enums import OrderSide, PortfolioTarget
from libs.common.models.order import Fill

from agents.reconciliation.coinbase_reconciler import (
    find_orphaned_orders,
    reconcile_positions,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _amount(value: Decimal | str, currency: str = "USD") -> Amount:
    return Amount(value=str(value), currency=currency)


def _fill(
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("2200"),
    instrument: str = "ETH-PERP",
    order_id: str = "ord-1",
) -> Fill:
    return Fill(
        fill_id=f"fill-{order_id}",
        order_id=order_id,
        portfolio_target=PortfolioTarget.A,
        instrument=instrument,
        side=side,
        size=size,
        price=price,
        fee_usdc=Decimal("0.55"),
        is_maker=True,
        filled_at=T0,
        trade_id=f"trade-{order_id}",
    )


def _position_resp(
    side: str = "LONG",
    net_size: Decimal = Decimal("2.0"),
) -> PositionResponse:
    return PositionResponse(
        product_id="ETH-PERP",
        portfolio_uuid="test-pid",
        position_side=side,
        net_size=str(net_size),
        entry_vwap=_amount(Decimal("2200")),
        mark_price=_amount(Decimal("2250")),
        unrealized_pnl=_amount(Decimal("100")),
    )


class TestReconcilePositions:
    def test_consistent_when_matching(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("2.0"), order_id="o1"),
        ]
        positions = [_position_resp(side="LONG", net_size=Decimal("2.0"))]
        result = reconcile_positions(fills, positions, PortfolioTarget.A)
        assert result.is_consistent is True
        assert len(result.discrepancies) == 0

    def test_size_discrepancy_detected(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("1.5"), order_id="o1"),
        ]
        positions = [_position_resp(side="LONG", net_size=Decimal("2.0"))]
        result = reconcile_positions(fills, positions, PortfolioTarget.A)
        assert result.is_consistent is False
        assert any(d.field == "size" for d in result.discrepancies)

    def test_side_discrepancy_detected(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("2.0"), order_id="o1"),
        ]
        positions = [_position_resp(side="SHORT", net_size=Decimal("-2.0"))]
        result = reconcile_positions(fills, positions, PortfolioTarget.A)
        assert result.is_consistent is False
        assert any(d.field == "side" for d in result.discrepancies)

    def test_within_tolerance(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("1.9995"), order_id="o1"),
        ]
        positions = [_position_resp(side="LONG", net_size=Decimal("2.0"))]
        result = reconcile_positions(
            fills, positions, PortfolioTarget.A,
            tolerance=Decimal("0.001"),
        )
        assert result.is_consistent is True

    def test_net_position_from_multiple_fills(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("1.0"), order_id="o1"),
            _fill(side=OrderSide.BUY, size=Decimal("1.0"), order_id="o2"),
        ]
        positions = [_position_resp(side="LONG", net_size=Decimal("2.0"))]
        result = reconcile_positions(fills, positions, PortfolioTarget.A)
        assert result.is_consistent is True

    def test_partial_close_net_position(self) -> None:
        fills = [
            _fill(side=OrderSide.BUY, size=Decimal("3.0"), order_id="o1"),
            _fill(side=OrderSide.SELL, size=Decimal("1.0"), order_id="o2"),
        ]
        positions = [_position_resp(side="LONG", net_size=Decimal("2.0"))]
        result = reconcile_positions(fills, positions, PortfolioTarget.A)
        assert result.is_consistent is True

    def test_empty_fills_and_positions(self) -> None:
        result = reconcile_positions([], [], PortfolioTarget.A)
        assert result.is_consistent is True


class TestFindOrphanedOrders:
    def test_no_orphans(self) -> None:
        internal_ids = {"ord-1", "ord-2"}
        exchange_orders = [
            OrderResponse(
                order_id="exch-1",
                client_order_id="ord-1",
                product_id="ETH-PERP",
                side="BUY",
                order_type="LIMIT",
                base_size="1",
                status="OPEN",
            ),
        ]
        orphans = find_orphaned_orders(internal_ids, exchange_orders)
        assert len(orphans) == 0

    def test_orphan_detected(self) -> None:
        internal_ids = {"ord-1"}
        exchange_orders = [
            OrderResponse(
                order_id="exch-1",
                client_order_id="ord-1",
                product_id="ETH-PERP",
                side="BUY",
                order_type="LIMIT",
                base_size="1",
                status="OPEN",
            ),
            OrderResponse(
                order_id="exch-2",
                client_order_id="unknown-ord",
                product_id="ETH-PERP",
                side="SELL",
                order_type="LIMIT",
                base_size="0.5",
                status="OPEN",
            ),
        ]
        orphans = find_orphaned_orders(internal_ids, exchange_orders)
        assert orphans == ["exch-2"]
