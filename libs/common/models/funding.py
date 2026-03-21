"""Funding rate and funding payment models (hourly USDC settlements)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget, PositionSide


@dataclass(frozen=True, slots=True)
class FundingRate:
    """Current or historical hourly funding rate for ETH-PERP."""

    timestamp: datetime
    instrument: str
    rate: Decimal
    next_settlement_time: datetime
    mark_price: Decimal
    index_price: Decimal

    @property
    def annualized_rate(self) -> Decimal:
        """Annualized funding rate (hourly rate * 24 * 365)."""
        return self.rate * 24 * 365

    @property
    def is_positive(self) -> bool:
        """When positive, longs pay shorts."""
        return self.rate > 0


@dataclass(frozen=True, slots=True)
class FundingPayment:
    """A single hourly funding settlement for a portfolio's position."""

    timestamp: datetime
    instrument: str
    portfolio_target: PortfolioTarget
    rate: Decimal
    payment_usdc: Decimal
    position_size: Decimal
    position_side: PositionSide
    cumulative_24h_usdc: Decimal
