"""Tests for execution algorithm selection."""

from decimal import Decimal

from libs.common.models.enums import OrderSide, OrderType

from agents.execution.algo_selector import (
    ExecutionPlan,
    compute_slippage_bps,
    select_algo,
)


class TestSelectAlgo:
    def test_explicit_limit_price_used(self) -> None:
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.LIMIT,
            explicit_limit_price=Decimal("2200.00"),
        )
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price == Decimal("2200.00")
        assert plan.is_maker is True

    def test_limit_buy_from_orderbook(self) -> None:
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.LIMIT,
            best_bid=Decimal("2200.00"),
            best_ask=Decimal("2201.00"),
            limit_offset_bps=5,
        )
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price is not None
        # 2200 + 2200*5/10000 = 2201.10, but clamped to best_ask - tick = 2200.99
        assert plan.limit_price == Decimal("2200.99")
        assert plan.is_maker is True

    def test_limit_sell_from_orderbook(self) -> None:
        plan = select_algo(
            side=OrderSide.SELL,
            requested_type=OrderType.LIMIT,
            best_bid=Decimal("2200.00"),
            best_ask=Decimal("2201.00"),
            limit_offset_bps=5,
        )
        assert plan.order_type == OrderType.LIMIT
        assert plan.limit_price is not None
        # 2201 - 2201*5/10000 = 2199.90, but clamped to best_bid + tick = 2200.01
        assert plan.limit_price == Decimal("2200.01")

    def test_market_order_when_requested_and_no_book(self) -> None:
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.MARKET,
            prefer_maker=False,
        )
        assert plan.order_type == OrderType.MARKET
        assert plan.limit_price is None
        assert plan.is_maker is False

    def test_prefer_maker_upgrades_market_to_limit(self) -> None:
        """When prefer_maker=True and we have orderbook, upgrade MARKET to LIMIT."""
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.MARKET,
            best_bid=Decimal("2200.00"),
            best_ask=Decimal("2201.00"),
            prefer_maker=True,
        )
        assert plan.order_type == OrderType.LIMIT
        assert plan.is_maker is True

    def test_market_fallback_when_no_orderbook(self) -> None:
        """Even with prefer_maker, fall back to MARKET if no orderbook data."""
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.MARKET,
            prefer_maker=True,
        )
        assert plan.order_type == OrderType.MARKET
        assert plan.limit_price is None

    def test_stop_limit_passes_through(self) -> None:
        plan = select_algo(
            side=OrderSide.SELL,
            requested_type=OrderType.STOP_LIMIT,
            explicit_limit_price=Decimal("2100.00"),
        )
        assert plan.order_type == OrderType.STOP_LIMIT
        assert plan.limit_price == Decimal("2100.00")

    def test_stop_market_passes_through(self) -> None:
        plan = select_algo(
            side=OrderSide.SELL,
            requested_type=OrderType.STOP_MARKET,
        )
        assert plan.order_type == OrderType.STOP_MARKET
        assert plan.limit_price is None

    def test_limit_price_rounded_to_tick(self) -> None:
        """Limit prices should be rounded to TICK_SIZE (0.01)."""
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.LIMIT,
            explicit_limit_price=Decimal("2200.12345"),
        )
        assert plan.limit_price == Decimal("2200.12")

    def test_zero_offset_bps(self) -> None:
        plan = select_algo(
            side=OrderSide.BUY,
            requested_type=OrderType.LIMIT,
            best_bid=Decimal("2200.00"),
            limit_offset_bps=0,
        )
        assert plan.limit_price == Decimal("2200.00")


class TestComputeSlippage:
    def test_no_slippage(self) -> None:
        assert compute_slippage_bps(
            Decimal("2200"), Decimal("2200"), OrderSide.BUY,
        ) == 0

    def test_buy_positive_slippage(self) -> None:
        # Paid more than expected
        bps = compute_slippage_bps(
            Decimal("2200"), Decimal("2204.40"), OrderSide.BUY,
        )
        assert bps == 20  # 4.40/2200 * 10000 = 20 bps

    def test_buy_negative_slippage(self) -> None:
        # Paid less than expected (price improvement)
        bps = compute_slippage_bps(
            Decimal("2200"), Decimal("2197.80"), OrderSide.BUY,
        )
        assert bps == -10  # -2.20/2200 * 10000 = -10 bps

    def test_sell_positive_slippage(self) -> None:
        # Received less than expected
        bps = compute_slippage_bps(
            Decimal("2200"), Decimal("2195.60"), OrderSide.SELL,
        )
        assert bps == 20  # sold for less = bad

    def test_zero_expected_price(self) -> None:
        assert compute_slippage_bps(
            Decimal("0"), Decimal("2200"), OrderSide.BUY,
        ) == 0
