"""Order placement orchestration.

Coordinates the full order lifecycle:
  algo selection → place order → handle response → retry on failure
  → place protective orders (SL/TP) after fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import structlog

from libs.common.models.enums import OrderSide, OrderStatus, OrderType, PortfolioTarget
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder
from libs.common.utils import utc_now
from libs.coinbase.models import OrderResponse

logger = structlog.get_logger(__name__)

from agents.execution.algo_selector import ExecutionPlan, select_algo
from agents.execution.config import ExecutionConfig
from agents.execution.stop_loss_manager import ProtectiveOrders, build_protective_orders


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Outcome of an order placement attempt."""

    order_id: str
    exchange_order_id: str
    status: OrderStatus
    filled_size: Decimal
    average_price: Decimal | None
    fee_usdc: Decimal
    is_maker: bool
    protective_orders: ProtectiveOrders | None


def plan_from_proposed(
    order: ProposedOrder,
    config: ExecutionConfig,
    *,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
) -> ExecutionPlan:
    """Build an execution plan from a ProposedOrder (Portfolio A path)."""
    return select_algo(
        side=order.side,
        requested_type=order.order_type,
        best_bid=best_bid,
        best_ask=best_ask,
        limit_offset_bps=config.limit_offset_bps,
        prefer_maker=config.prefer_maker,
        explicit_limit_price=order.limit_price,
    )


def plan_from_approved(
    order: ApprovedOrder,
    config: ExecutionConfig,
    *,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
) -> ExecutionPlan:
    """Build an execution plan from an ApprovedOrder (Portfolio B path)."""
    return select_algo(
        side=order.side,
        requested_type=order.order_type,
        best_bid=best_bid,
        best_ask=best_ask,
        limit_offset_bps=config.limit_offset_bps,
        prefer_maker=config.prefer_maker,
        explicit_limit_price=order.limit_price,
    )


def build_result_from_response(
    order_id: str,
    response: OrderResponse,
    is_maker: bool,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
) -> ExecutionResult:
    """Convert a Coinbase OrderResponse into our ExecutionResult.

    Also builds protective orders if the primary order is filled.
    """
    status = _map_exchange_status(response.status)
    filled = Decimal(response.filled_size) if response.filled_size else Decimal("0")
    avg_price_str = response.average_filled_price
    avg_price = Decimal(avg_price_str) if avg_price_str and avg_price_str != "0" else None
    fee = Decimal(response.total_fees) if response.total_fees else Decimal("0")

    protective = None
    if filled > 0 and (stop_loss or take_profit):
        fill_side = OrderSide(response.side)
        protective = build_protective_orders(
            fill_side=fill_side,
            fill_size=filled,
            fill_price=avg_price or Decimal("0"),
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
        )

    return ExecutionResult(
        order_id=order_id,
        exchange_order_id=response.order_id,
        status=status,
        filled_size=filled,
        average_price=avg_price,
        fee_usdc=fee,
        is_maker=is_maker,
        protective_orders=protective,
    )


def build_fill_from_response(
    order_id: str,
    portfolio_target: PortfolioTarget,
    response: OrderResponse,
    is_maker: bool,
    now: datetime | None = None,
) -> Fill | None:
    """Build a Fill from an exchange OrderResponse, if any quantity was filled."""
    filled = Decimal(response.filled_size) if response.filled_size else Decimal("0")
    avg_price_str = response.average_filled_price
    avg_price = Decimal(avg_price_str) if avg_price_str and avg_price_str != "0" else None
    if filled <= 0 or avg_price is None:
        return None
    now = now or utc_now()
    fee = Decimal(response.total_fees) if response.total_fees else Decimal("0")
    return Fill(
        fill_id=f"fill-{response.order_id}",
        order_id=order_id,
        portfolio_target=portfolio_target,
        instrument=response.product_id,
        side=OrderSide(response.side),
        size=filled,
        price=avg_price,
        fee_usdc=fee,
        is_maker=is_maker,
        filled_at=now,
        trade_id=response.order_id,
    )


def _map_exchange_status(exchange_status: str) -> OrderStatus:
    """Map Coinbase exchange order status to our OrderStatus enum."""
    mapping = {
        "OPEN": OrderStatus.OPEN,
        "FILLED": OrderStatus.FILLED,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "EXPIRED": OrderStatus.EXPIRED,
        "REJECTED": OrderStatus.REJECTED_BY_EXCHANGE,
        "PENDING": OrderStatus.SENT_TO_EXCHANGE,
    }
    mapped = mapping.get(exchange_status.upper())
    if mapped is None:
        logger.warning(
            "unknown_exchange_status",
            exchange_status=exchange_status,
            defaulting_to="SENT_TO_EXCHANGE",
        )
        return OrderStatus.SENT_TO_EXCHANGE
    return mapped
