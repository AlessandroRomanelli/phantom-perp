"""Order lifecycle models."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)


@dataclass(slots=True)
class ProposedOrder:
    """An order that has been sized, risk-checked, and is ready for routing.

    For Portfolio A: goes directly to execution.
    For Portfolio B: goes to the confirmation agent first.
    """

    order_id: str
    signal_id: str
    instrument: str
    portfolio_target: PortfolioTarget
    side: OrderSide
    size: Decimal
    order_type: OrderType
    conviction: float
    sources: list[SignalSource]
    estimated_margin_required_usdc: Decimal
    estimated_liquidation_price: Decimal
    estimated_fee_usdc: Decimal
    estimated_funding_cost_1h_usdc: Decimal
    proposed_at: datetime
    limit_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    leverage: Decimal = Decimal("1")
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.RISK_APPROVED
    reasoning: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def notional_usdc(self) -> Decimal:
        """Estimated notional value of the order."""
        price = self.limit_price or Decimal("0")
        return self.size * price


@dataclass(frozen=True, slots=True)
class ApprovedOrder:
    """An order that has been approved (auto for A, user-confirmed for B)."""

    order_id: str
    portfolio_target: PortfolioTarget
    instrument: str
    side: OrderSide
    size: Decimal
    order_type: OrderType
    limit_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    leverage: Decimal
    reduce_only: bool
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class Fill:
    """A fill event from the exchange."""

    fill_id: str
    order_id: str
    portfolio_target: PortfolioTarget
    instrument: str
    side: OrderSide
    size: Decimal
    price: Decimal
    fee_usdc: Decimal
    is_maker: bool
    filled_at: datetime
    trade_id: str
