"""Fee tracking and reporting — maker/taker breakdown per portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget
from libs.common.models.order import Fill


@dataclass(frozen=True, slots=True)
class FeeSummary:
    """Aggregated fee metrics over a time window."""

    portfolio_target: PortfolioTarget
    window_label: str
    total_fees_usdc: Decimal
    maker_fees_usdc: Decimal
    taker_fees_usdc: Decimal
    fill_count: int
    maker_count: int
    taker_count: int
    maker_ratio: float
    estimated_savings_usdc: Decimal


@dataclass(slots=True)
class FeeTracker:
    """Accumulates fill fee data and produces fee reports.

    Tracks maker vs taker fills and computes how much was saved by
    using limit (maker) orders vs hypothetical taker fees.
    """

    portfolio_target: PortfolioTarget
    taker_rate: Decimal = Decimal("0.000250")
    max_history_hours: int = 168  # 7 days
    _fills: list[Fill] = field(default_factory=list)

    def record_fill(self, fill: Fill) -> None:
        """Add a fill and prune old entries."""
        self._fills.append(fill)
        self._prune()

    def _prune(self) -> None:
        if not self._fills:
            return
        latest = self._fills[-1].filled_at
        cutoff = latest - timedelta(hours=self.max_history_hours)
        self._fills = [f for f in self._fills if f.filled_at >= cutoff]

    @property
    def fill_count(self) -> int:
        return len(self._fills)

    def _windowed(self, hours: int) -> list[Fill]:
        if not self._fills:
            return []
        latest = self._fills[-1].filled_at
        cutoff = latest - timedelta(hours=hours)
        return [f for f in self._fills if f.filled_at >= cutoff]

    def daily_summary(self) -> FeeSummary:
        """Fee summary for the last 24 hours."""
        return self._build_summary(self._windowed(24), "24h")

    def weekly_summary(self) -> FeeSummary:
        """Fee summary for the last 7 days."""
        return self._build_summary(self._windowed(168), "7d")

    def _build_summary(self, fills: list[Fill], label: str) -> FeeSummary:
        if not fills:
            return FeeSummary(
                portfolio_target=self.portfolio_target,
                window_label=label,
                total_fees_usdc=Decimal("0"),
                maker_fees_usdc=Decimal("0"),
                taker_fees_usdc=Decimal("0"),
                fill_count=0,
                maker_count=0,
                taker_count=0,
                maker_ratio=0.0,
                estimated_savings_usdc=Decimal("0"),
            )

        maker_fills = [f for f in fills if f.is_maker]
        taker_fills = [f for f in fills if not f.is_maker]

        maker_fees = sum((f.fee_usdc for f in maker_fills), Decimal("0"))
        taker_fees = sum((f.fee_usdc for f in taker_fills), Decimal("0"))
        total = maker_fees + taker_fees

        # Savings: what maker fills would have cost at taker rate
        hypothetical_taker = sum(
            (f.size * f.price * self.taker_rate for f in maker_fills),
            Decimal("0"),
        )
        savings = hypothetical_taker - maker_fees

        return FeeSummary(
            portfolio_target=self.portfolio_target,
            window_label=label,
            total_fees_usdc=total,
            maker_fees_usdc=maker_fees,
            taker_fees_usdc=taker_fees,
            fill_count=len(fills),
            maker_count=len(maker_fills),
            taker_count=len(taker_fills),
            maker_ratio=len(maker_fills) / len(fills) if fills else 0.0,
            estimated_savings_usdc=savings,
        )


@dataclass(slots=True)
class DualFeeTracker:
    """Manages independent fee trackers for both portfolios."""

    tracker_a: FeeTracker = field(
        default_factory=lambda: FeeTracker(portfolio_target=PortfolioTarget.A),
    )
    tracker_b: FeeTracker = field(
        default_factory=lambda: FeeTracker(portfolio_target=PortfolioTarget.B),
    )

    def get_tracker(self, target: PortfolioTarget) -> FeeTracker:
        return self.tracker_a if target == PortfolioTarget.A else self.tracker_b

    @property
    def combined_daily_fees_usdc(self) -> Decimal:
        return (
            self.tracker_a.daily_summary().total_fees_usdc
            + self.tracker_b.daily_summary().total_fees_usdc
        )
