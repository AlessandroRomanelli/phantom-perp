"""Stop-loss and take-profit order management.

After a primary order fills, protective orders are placed on the exchange.
Every position MUST have a stop-loss — this is a non-negotiable safety rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import structlog

from libs.common.models.enums import OrderSide, OrderType

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProtectiveOrderParams:
    """Parameters for a protective (SL/TP) order to place on the exchange."""

    side: OrderSide
    size: Decimal
    order_type: OrderType
    stop_price: Decimal
    limit_price: Decimal | None  # For STOP_LIMIT orders
    reduce_only: bool = True


@dataclass(frozen=True, slots=True)
class ProtectiveOrders:
    """Stop-loss and take-profit order params for a filled position."""

    stop_loss: ProtectiveOrderParams | None
    take_profit: ProtectiveOrderParams | None


def build_protective_orders(
    fill_side: OrderSide,
    fill_size: Decimal,
    fill_price: Decimal,
    stop_loss_price: Decimal | None,
    take_profit_price: Decimal | None,
    tick_size: Decimal = Decimal("0.01"),
) -> ProtectiveOrders:
    """Build protective order parameters after a primary fill.

    Args:
        fill_side: Side of the filled primary order (BUY/SELL).
        fill_size: Size of the filled position.
        fill_price: Price the primary order filled at (for SL validation).
        stop_loss_price: Stop-loss trigger price.
        take_profit_price: Take-profit trigger price.

    Returns:
        ProtectiveOrders with SL and optionally TP params.
    """
    # Protective orders close the position — opposite side
    close_side = OrderSide.SELL if fill_side == OrderSide.BUY else OrderSide.BUY

    sl = None
    if stop_loss_price is not None:
        if not validate_stop_loss_required(stop_loss_price, fill_side, fill_price):
            logger.warning(
                "stop_loss_wrong_side",
                fill_side=fill_side.value,
                fill_price=str(fill_price),
                stop_loss_price=str(stop_loss_price),
            )
        else:
            sl = ProtectiveOrderParams(
                side=close_side,
                size=fill_size,
                order_type=OrderType.STOP_MARKET,
                stop_price=_round_to_tick(stop_loss_price, tick_size),
                limit_price=None,
                reduce_only=True,
            )

    tp = None
    if take_profit_price is not None:
        tp = ProtectiveOrderParams(
            side=close_side,
            size=fill_size,
            order_type=OrderType.LIMIT,
            stop_price=_round_to_tick(take_profit_price, tick_size),
            limit_price=_round_to_tick(take_profit_price, tick_size),
            reduce_only=True,
        )

    return ProtectiveOrders(stop_loss=sl, take_profit=tp)


def validate_stop_loss_required(
    stop_loss_price: Decimal | None,
    fill_side: OrderSide,
    fill_price: Decimal,
) -> bool:
    """Validate that a stop-loss is present and on the correct side.

    Non-negotiable rule: every position MUST have a stop-loss.

    Returns:
        True if the stop-loss is valid.
    """
    if stop_loss_price is None:
        return False

    # For LONG (BUY), stop-loss must be below fill price
    if fill_side == OrderSide.BUY:
        return stop_loss_price < fill_price

    # For SHORT (SELL), stop-loss must be above fill price
    return stop_loss_price > fill_price


def _round_to_tick(price: Decimal, tick_size: Decimal = Decimal("0.01")) -> Decimal:
    return (price / tick_size).quantize(Decimal("1")) * tick_size
