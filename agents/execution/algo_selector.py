"""Execution algorithm selection — choose order type and compute limit price.

Defaults to LIMIT (maker, 0.0125%) over MARKET (taker, 0.0250%).
For LIMIT orders, prices are offset from the best bid/ask by a configurable
number of basis points to improve fill probability while staying maker.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.common.constants import TICK_SIZE
from libs.common.models.enums import OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Result of algo selection — what order to send to the exchange."""

    order_type: OrderType
    limit_price: Decimal | None
    is_maker: bool


def select_algo(
    side: OrderSide,
    requested_type: OrderType,
    *,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
    limit_offset_bps: int = 5,
    prefer_maker: bool = True,
    explicit_limit_price: Decimal | None = None,
) -> ExecutionPlan:
    """Select execution algorithm and compute the limit price.

    Args:
        side: BUY or SELL.
        requested_type: Order type from the risk/confirmation agent.
        best_bid: Current best bid price (for LIMIT buy price computation).
        best_ask: Current best ask price (for LIMIT sell price computation).
        limit_offset_bps: Basis points to offset from best bid/ask.
        prefer_maker: If True, prefer LIMIT even when MARKET was requested.
        explicit_limit_price: If provided, use this price instead of computing.

    Returns:
        ExecutionPlan with the order type and computed limit price.
    """
    # STOP_LIMIT and STOP_MARKET pass through — these are protective orders
    if requested_type in (OrderType.STOP_LIMIT, OrderType.STOP_MARKET):
        return ExecutionPlan(
            order_type=requested_type,
            limit_price=explicit_limit_price,
            is_maker=requested_type == OrderType.STOP_LIMIT,
        )

    # If an explicit limit price was provided, use it directly
    if explicit_limit_price is not None:
        return ExecutionPlan(
            order_type=OrderType.LIMIT,
            limit_price=_round_to_tick(explicit_limit_price),
            is_maker=True,
        )

    # Try to use LIMIT if we prefer maker and have orderbook data
    if prefer_maker and requested_type in (OrderType.LIMIT, OrderType.MARKET):
        computed = _compute_limit_price(side, best_bid, best_ask, limit_offset_bps)
        if computed is not None:
            return ExecutionPlan(
                order_type=OrderType.LIMIT,
                limit_price=computed,
                is_maker=True,
            )

    # MARKET order — no limit price
    if requested_type == OrderType.MARKET:
        return ExecutionPlan(
            order_type=OrderType.MARKET,
            limit_price=None,
            is_maker=False,
        )

    # LIMIT requested but no orderbook data — fall back to MARKET
    return ExecutionPlan(
        order_type=OrderType.MARKET,
        limit_price=None,
        is_maker=False,
    )


def compute_slippage_bps(
    expected_price: Decimal,
    actual_price: Decimal,
    side: OrderSide,
) -> int:
    """Compute slippage in basis points.

    Positive slippage means worse execution than expected.

    Args:
        expected_price: The price we expected to execute at.
        actual_price: The price we actually executed at.
        side: BUY or SELL.

    Returns:
        Slippage in basis points (positive = worse, negative = better).
    """
    if expected_price <= 0:
        return 0
    diff = actual_price - expected_price
    # For BUY: paying more is bad (positive slippage)
    # For SELL: receiving less is bad (positive slippage)
    if side == OrderSide.SELL:
        diff = -diff
    return int(diff / expected_price * Decimal("10000"))


def _compute_limit_price(
    side: OrderSide,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
    offset_bps: int,
) -> Decimal | None:
    """Compute a limit price offset from the current best bid/ask.

    For BUY: price slightly above best bid (to stay near top of book).
    For SELL: price slightly below best ask (to stay near top of book).
    """
    if side == OrderSide.BUY and best_bid is not None:
        offset = best_bid * Decimal(offset_bps) / Decimal("10000")
        return _round_to_tick(best_bid + offset)
    if side == OrderSide.SELL and best_ask is not None:
        offset = best_ask * Decimal(offset_bps) / Decimal("10000")
        return _round_to_tick(best_ask - offset)
    return None


def _round_to_tick(price: Decimal) -> Decimal:
    """Round a price to the nearest tick size."""
    return (price / TICK_SIZE).quantize(Decimal("1")) * TICK_SIZE
