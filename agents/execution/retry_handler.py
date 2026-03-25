"""Retry logic for rejected orders.

Handles exchange rejections, insufficient margin, and rate limits
with configurable retry policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.common.exceptions import (
    InsufficientMarginError,
    OrderRejectedError,
    RateLimitExceededError,
)
from libs.common.models.enums import OrderSide

from agents.execution.algo_selector import ExecutionPlan
from agents.execution.config import ExecutionConfig


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Whether and how to retry a failed order."""

    should_retry: bool
    reason: str
    adjusted_plan: ExecutionPlan | None = None
    wait_seconds: float = 0.0


def evaluate_retry(
    error: Exception,
    attempt: int,
    config: ExecutionConfig,
    current_plan: ExecutionPlan,
    side: OrderSide | None = None,
) -> RetryDecision:
    """Decide whether to retry a failed order placement.

    Args:
        error: The exception that caused the failure.
        attempt: Current attempt number (0-based, so first try is 0).
        config: Execution configuration.
        current_plan: The execution plan that failed.
        side: Order side (BUY/SELL) — required for correct price adjustment.

    Returns:
        RetryDecision indicating whether to retry and any adjustments.
    """
    if attempt >= config.max_retries:
        return RetryDecision(
            should_retry=False,
            reason=f"max retries ({config.max_retries}) exhausted",
        )

    if not config.retry_on_rejection:
        return RetryDecision(
            should_retry=False,
            reason="retry_on_rejection is disabled",
        )

    if isinstance(error, RateLimitExceededError):
        # Cap wait to 30s to prevent absurdly long sleeps from bad retry_after values
        raw_wait = error.retry_after or 1.0
        wait = min(float(raw_wait), 30.0)
        return RetryDecision(
            should_retry=True,
            reason="rate limited, waiting",
            adjusted_plan=current_plan,
            wait_seconds=wait,
        )

    if isinstance(error, InsufficientMarginError):
        # Don't retry — margin won't magically appear
        return RetryDecision(
            should_retry=False,
            reason="insufficient margin — cannot retry",
        )

    if isinstance(error, OrderRejectedError):
        # Try adjusting the limit price slightly for the retry
        adjusted = _adjust_price_for_retry(current_plan, attempt, side=side)
        return RetryDecision(
            should_retry=True,
            reason=f"order rejected, adjusting price (attempt {attempt + 1})",
            adjusted_plan=adjusted,
            wait_seconds=0.5,
        )

    # Unknown error — don't retry
    return RetryDecision(
        should_retry=False,
        reason=f"unhandled error type: {type(error).__name__}",
    )


def _adjust_price_for_retry(
    plan: ExecutionPlan,
    attempt: int,
    tick_size: Decimal = Decimal("0.01"),
    side: OrderSide | None = None,
) -> ExecutionPlan:
    """Adjust the execution plan for a retry attempt.

    For LIMIT orders, widen the price slightly to improve fill probability.
    BUY orders: raise price (more aggressive). SELL orders: lower price (more aggressive).
    """
    from libs.common.models.enums import OrderType

    if plan.order_type != OrderType.LIMIT or plan.limit_price is None:
        return plan

    # Each retry widens the price by 1 tick
    adjustment = tick_size * Decimal(attempt + 1)
    # BUY: raise price to be more aggressive; SELL: lower price to be more aggressive
    if side == OrderSide.SELL:
        new_price = plan.limit_price - adjustment
    else:
        # BUY or unknown side — raise price (preserves legacy behavior for BUY)
        new_price = plan.limit_price + adjustment
    return ExecutionPlan(
        order_type=plan.order_type,
        limit_price=new_price,
        is_maker=plan.is_maker,
    )
