"""Tests for order placement orchestration."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, ProposedOrder
from libs.coinbase.models import OrderResponse

from agents.execution.config import ExecutionConfig
from agents.execution.order_placer import (
    build_fill_from_response,
    build_result_from_response,
    plan_from_approved,
    plan_from_proposed,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _proposed_order(**overrides: object) -> ProposedOrder:
    defaults = dict(
        order_id="ord-001",
        signal_id="sig-001",
        instrument="ETH-PERP",
        portfolio_target=PortfolioTarget.A,
        side=OrderSide.BUY,
        size=Decimal("2.5"),
        order_type=OrderType.LIMIT,
        conviction=0.82,
        sources=[SignalSource.MOMENTUM],
        estimated_margin_required_usdc=Decimal("1700"),
        estimated_liquidation_price=Decimal("1890"),
        estimated_fee_usdc=Decimal("0.70"),
        estimated_funding_cost_1h_usdc=Decimal("-0.08"),
        proposed_at=T0,
        limit_price=Decimal("2200"),
        stop_loss=Decimal("2100"),
        take_profit=Decimal("2400"),
        leverage=Decimal("3"),
    )
    defaults.update(overrides)
    return ProposedOrder(**defaults)  # type: ignore[arg-type]


def _approved_order(**overrides: object) -> ApprovedOrder:
    defaults = dict(
        order_id="ord-002",
        portfolio_target=PortfolioTarget.B,
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
    defaults.update(overrides)
    return ApprovedOrder(**defaults)  # type: ignore[arg-type]


def _exchange_response(
    status: str = "FILLED",
    filled_size: Decimal = Decimal("2.5"),
    avg_price: Decimal | None = Decimal("2200"),
    fee: Decimal = Decimal("0.69"),
) -> OrderResponse:
    return OrderResponse(
        order_id="exch-ord-001",
        client_order_id="ord-001",
        product_id="ETH-PERP",
        side="BUY",
        order_type="LIMIT",
        base_size=str(Decimal("2.5")),
        limit_price=str(Decimal("2200")),
        status=status,
        filled_size=str(filled_size),
        filled_value=str(filled_size * (avg_price or Decimal("0"))),
        average_filled_price=str(avg_price) if avg_price is not None else "0",
        total_fees=str(fee),
    )


class TestPlanFromProposed:
    def test_uses_explicit_limit_price(self) -> None:
        config = ExecutionConfig()
        plan = plan_from_proposed(_proposed_order(), config)
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price == Decimal("2200")
        assert plan.is_maker is True

    def test_computes_price_from_orderbook(self) -> None:
        config = ExecutionConfig()
        order = _proposed_order(limit_price=None)
        plan = plan_from_proposed(
            order, config,
            best_bid=Decimal("2200"),
            best_ask=Decimal("2201"),
        )
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price is not None


class TestPlanFromApproved:
    def test_uses_explicit_limit_price(self) -> None:
        config = ExecutionConfig()
        plan = plan_from_approved(_approved_order(), config)
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price == Decimal("2200")

    def test_market_order_when_no_price(self) -> None:
        config = ExecutionConfig(prefer_maker=False)
        order = _approved_order(
            order_type=OrderType.MARKET,
            limit_price=None,
        )
        plan = plan_from_approved(order, config)
        assert plan.order_type == OrderType.MARKET


class TestBuildResultFromResponse:
    def test_filled_order_produces_result(self) -> None:
        result = build_result_from_response(
            order_id="ord-001",
            response=_exchange_response(status="FILLED"),
            is_maker=True,
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
        )
        assert result.order_id == "ord-001"
        assert result.exchange_order_id == "exch-ord-001"
        assert result.status == OrderStatus.FILLED
        assert result.filled_size == Decimal("2.5")
        assert result.average_price == Decimal("2200")
        assert result.fee_usdc == Decimal("0.69")
        assert result.is_maker is True

    def test_filled_order_has_protective_orders(self) -> None:
        result = build_result_from_response(
            order_id="ord-001",
            response=_exchange_response(status="FILLED"),
            is_maker=True,
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
        )
        assert result.protective_orders is not None
        assert result.protective_orders.stop_loss is not None
        assert result.protective_orders.stop_loss.side == OrderSide.SELL
        assert result.protective_orders.take_profit is not None

    def test_unfilled_order_no_protective_orders(self) -> None:
        result = build_result_from_response(
            order_id="ord-001",
            response=_exchange_response(
                status="OPEN",
                filled_size=Decimal("0"),
                avg_price=None,
            ),
            is_maker=True,
            stop_loss=Decimal("2100"),
            take_profit=None,
        )
        assert result.protective_orders is None

    def test_status_mapping(self) -> None:
        for exchange_status, expected in [
            ("FILLED", OrderStatus.FILLED),
            ("OPEN", OrderStatus.OPEN),
            ("CANCELLED", OrderStatus.CANCELLED),
            ("REJECTED", OrderStatus.REJECTED_BY_EXCHANGE),
            ("PARTIALLY_FILLED", OrderStatus.PARTIALLY_FILLED),
        ]:
            result = build_result_from_response(
                order_id="ord",
                response=_exchange_response(status=exchange_status, filled_size=Decimal("0"), avg_price=None),
                is_maker=True,
                stop_loss=None,
                take_profit=None,
            )
            assert result.status == expected, f"Failed for {exchange_status}"


class TestBuildFillFromResponse:
    def test_creates_fill_when_filled(self) -> None:
        fill = build_fill_from_response(
            order_id="ord-001",
            portfolio_target=PortfolioTarget.A,
            response=_exchange_response(),
            is_maker=True,
            now=T0,
        )
        assert fill is not None
        assert fill.order_id == "ord-001"
        assert fill.portfolio_target == PortfolioTarget.A
        assert fill.side == OrderSide.BUY
        assert fill.size == Decimal("2.5")
        assert fill.price == Decimal("2200")
        assert fill.fee_usdc == Decimal("0.69")
        assert fill.is_maker is True

    def test_returns_none_when_not_filled(self) -> None:
        fill = build_fill_from_response(
            order_id="ord-001",
            portfolio_target=PortfolioTarget.A,
            response=_exchange_response(
                status="OPEN",
                filled_size=Decimal("0"),
                avg_price=None,
            ),
            is_maker=True,
        )
        assert fill is None
