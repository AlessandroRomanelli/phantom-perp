"""Tests for stop-loss and take-profit order management."""

from decimal import Decimal

from libs.common.models.enums import OrderSide, OrderType

from agents.execution.stop_loss_manager import (
    build_protective_orders,
    validate_stop_loss_required,
)


class TestBuildProtectiveOrders:
    def test_long_position_sl_is_sell(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("2.5"),
            fill_price=Decimal("2200"),
            stop_loss_price=Decimal("2100"),
            take_profit_price=Decimal("2400"),
        )
        assert result.stop_loss is not None
        assert result.stop_loss.side == OrderSide.SELL
        assert result.stop_loss.size == Decimal("2.5")
        assert result.stop_loss.order_type == OrderType.STOP_MARKET
        assert result.stop_loss.stop_price == Decimal("2100")
        assert result.stop_loss.reduce_only is True

    def test_short_position_sl_is_buy(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.SELL,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=Decimal("2300"),
            take_profit_price=Decimal("2000"),
        )
        assert result.stop_loss is not None
        assert result.stop_loss.side == OrderSide.BUY
        assert result.stop_loss.stop_price == Decimal("2300")

    def test_take_profit_is_limit_order(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=Decimal("2100"),
            take_profit_price=Decimal("2400"),
        )
        assert result.take_profit is not None
        assert result.take_profit.order_type == OrderType.LIMIT
        assert result.take_profit.limit_price == Decimal("2400")
        assert result.take_profit.side == OrderSide.SELL
        assert result.take_profit.reduce_only is True

    def test_no_take_profit(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=Decimal("2100"),
            take_profit_price=None,
        )
        assert result.stop_loss is not None
        assert result.take_profit is None

    def test_no_stop_loss(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=None,
            take_profit_price=Decimal("2400"),
        )
        assert result.stop_loss is None
        assert result.take_profit is not None

    def test_no_protective_orders(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=None,
            take_profit_price=None,
        )
        assert result.stop_loss is None
        assert result.take_profit is None

    def test_prices_rounded_to_tick(self) -> None:
        result = build_protective_orders(
            fill_side=OrderSide.BUY,
            fill_size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            stop_loss_price=Decimal("2100.12345"),
            take_profit_price=Decimal("2400.56789"),
        )
        assert result.stop_loss is not None
        assert result.stop_loss.stop_price == Decimal("2100.12")
        assert result.take_profit is not None
        assert result.take_profit.limit_price == Decimal("2400.57")


class TestValidateStopLoss:
    def test_valid_long_stop_loss(self) -> None:
        assert validate_stop_loss_required(
            Decimal("2100"), OrderSide.BUY, Decimal("2200"),
        ) is True

    def test_invalid_long_stop_loss_above_entry(self) -> None:
        assert validate_stop_loss_required(
            Decimal("2300"), OrderSide.BUY, Decimal("2200"),
        ) is False

    def test_valid_short_stop_loss(self) -> None:
        assert validate_stop_loss_required(
            Decimal("2300"), OrderSide.SELL, Decimal("2200"),
        ) is True

    def test_invalid_short_stop_loss_below_entry(self) -> None:
        assert validate_stop_loss_required(
            Decimal("2100"), OrderSide.SELL, Decimal("2200"),
        ) is False

    def test_none_stop_loss_is_invalid(self) -> None:
        assert validate_stop_loss_required(
            None, OrderSide.BUY, Decimal("2200"),
        ) is False
