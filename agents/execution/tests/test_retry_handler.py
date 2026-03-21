"""Tests for order retry logic."""

from decimal import Decimal

from libs.common.exceptions import (
    InsufficientMarginError,
    OrderRejectedError,
    RateLimitExceededError,
)
from libs.common.models.enums import OrderType

from agents.execution.algo_selector import ExecutionPlan
from agents.execution.config import ExecutionConfig
from agents.execution.retry_handler import evaluate_retry


def _plan(
    order_type: OrderType = OrderType.LIMIT,
    limit_price: Decimal | None = Decimal("2200"),
) -> ExecutionPlan:
    return ExecutionPlan(
        order_type=order_type,
        limit_price=limit_price,
        is_maker=order_type == OrderType.LIMIT,
    )


def _config(
    max_retries: int = 2,
    retry_on_rejection: bool = True,
) -> ExecutionConfig:
    return ExecutionConfig(
        max_retries=max_retries,
        retry_on_rejection=retry_on_rejection,
    )


class TestEvaluateRetry:
    def test_max_retries_exhausted(self) -> None:
        decision = evaluate_retry(
            error=OrderRejectedError(400, "rejected", "/orders"),
            attempt=2,
            config=_config(max_retries=2),
            current_plan=_plan(),
        )
        assert decision.should_retry is False
        assert "exhausted" in decision.reason

    def test_retry_disabled(self) -> None:
        decision = evaluate_retry(
            error=OrderRejectedError(400, "rejected", "/orders"),
            attempt=0,
            config=_config(retry_on_rejection=False),
            current_plan=_plan(),
        )
        assert decision.should_retry is False
        assert "disabled" in decision.reason

    def test_rate_limit_retries_with_wait(self) -> None:
        decision = evaluate_retry(
            error=RateLimitExceededError("/orders", retry_after=2.5),
            attempt=0,
            config=_config(),
            current_plan=_plan(),
        )
        assert decision.should_retry is True
        assert decision.wait_seconds == 2.5
        assert decision.adjusted_plan is not None

    def test_rate_limit_default_wait(self) -> None:
        decision = evaluate_retry(
            error=RateLimitExceededError("/orders", retry_after=None),
            attempt=0,
            config=_config(),
            current_plan=_plan(),
        )
        assert decision.should_retry is True
        assert decision.wait_seconds == 1.0

    def test_insufficient_margin_never_retries(self) -> None:
        decision = evaluate_retry(
            error=InsufficientMarginError(400, "insufficient margin", "/orders"),
            attempt=0,
            config=_config(),
            current_plan=_plan(),
        )
        assert decision.should_retry is False
        assert "margin" in decision.reason

    def test_order_rejected_retries_with_adjustment(self) -> None:
        decision = evaluate_retry(
            error=OrderRejectedError(422, "price too aggressive", "/orders"),
            attempt=0,
            config=_config(),
            current_plan=_plan(),
        )
        assert decision.should_retry is True
        assert decision.adjusted_plan is not None
        # Price should be adjusted
        assert decision.adjusted_plan.limit_price != _plan().limit_price

    def test_unknown_error_no_retry(self) -> None:
        decision = evaluate_retry(
            error=ValueError("something unexpected"),
            attempt=0,
            config=_config(),
            current_plan=_plan(),
        )
        assert decision.should_retry is False
        assert "unhandled" in decision.reason

    def test_market_order_retry_not_adjusted(self) -> None:
        """Market orders can't have their price adjusted."""
        plan = _plan(order_type=OrderType.MARKET, limit_price=None)
        decision = evaluate_retry(
            error=OrderRejectedError(400, "rejected", "/orders"),
            attempt=0,
            config=_config(),
            current_plan=plan,
        )
        assert decision.should_retry is True
        # Market order plan is returned as-is
        assert decision.adjusted_plan is not None
        assert decision.adjusted_plan.order_type == OrderType.MARKET
