"""Portfolio snapshot models — one per Coinbase portfolio."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget
from libs.common.models.position import PerpPosition


@dataclass(slots=True)
class PortfolioSnapshot:
    """Point-in-time state of a single Coinbase INTX portfolio."""

    timestamp: datetime
    portfolio_target: PortfolioTarget
    equity_usdc: Decimal
    used_margin_usdc: Decimal
    available_margin_usdc: Decimal
    margin_utilization_pct: float
    positions: list[PerpPosition]
    unrealized_pnl_usdc: Decimal
    realized_pnl_today_usdc: Decimal
    funding_pnl_today_usdc: Decimal
    fees_paid_today_usdc: Decimal

    @property
    def net_pnl_today_usdc(self) -> Decimal:
        """Net P&L today: realized + unrealized + funding - fees."""
        return (
            self.realized_pnl_today_usdc
            + self.unrealized_pnl_usdc
            + self.funding_pnl_today_usdc
            - self.fees_paid_today_usdc
        )

    @property
    def open_positions(self) -> list[PerpPosition]:
        """Positions that are currently active."""
        return [p for p in self.positions if p.is_open]


@dataclass(slots=True)
class SystemSnapshot:
    """Combined view of both portfolios — used by monitoring only."""

    timestamp: datetime
    portfolio_a: PortfolioSnapshot
    portfolio_b: PortfolioSnapshot

    @property
    def combined_equity_usdc(self) -> Decimal:
        return self.portfolio_a.equity_usdc + self.portfolio_b.equity_usdc

    @property
    def combined_unrealized_pnl_usdc(self) -> Decimal:
        return (
            self.portfolio_a.unrealized_pnl_usdc + self.portfolio_b.unrealized_pnl_usdc
        )

    @property
    def all_positions(self) -> list[PerpPosition]:
        """All open positions across both portfolios."""
        return self.portfolio_a.open_positions + self.portfolio_b.open_positions
