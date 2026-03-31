"""Perpetual futures position model."""

from dataclasses import dataclass
from decimal import Decimal

from libs.common.models.enums import PositionSide, Route


@dataclass(slots=True)
class PerpPosition:
    """A perpetual futures position in a specific Coinbase portfolio.

    All monetary values are in USDC.
    """

    instrument: str
    route: Route
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl_usdc: Decimal
    realized_pnl_usdc: Decimal
    leverage: Decimal
    initial_margin_usdc: Decimal
    maintenance_margin_usdc: Decimal
    liquidation_price: Decimal
    margin_ratio: float
    cumulative_funding_usdc: Decimal
    total_fees_usdc: Decimal

    @property
    def is_open(self) -> bool:
        """Whether this position is active (non-flat)."""
        return self.side != PositionSide.FLAT and self.size > 0

    @property
    def notional_usdc(self) -> Decimal:
        """Current notional value in USDC."""
        return self.size * self.mark_price

    @property
    def net_pnl_usdc(self) -> Decimal:
        """P&L net of funding and fees."""
        return (
            self.unrealized_pnl_usdc
            + self.realized_pnl_usdc
            + self.cumulative_funding_usdc
            - self.total_fees_usdc
        )
