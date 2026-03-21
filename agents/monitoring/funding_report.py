"""Funding payment aggregation and reporting per portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget
from libs.common.models.funding import FundingPayment


@dataclass(frozen=True, slots=True)
class FundingReportEntry:
    """A single row in a funding report — one payment."""

    timestamp: datetime
    rate: Decimal
    payment_usdc: Decimal
    position_size: Decimal


@dataclass(frozen=True, slots=True)
class FundingSummary:
    """Aggregated funding metrics over a time window."""

    portfolio_target: PortfolioTarget
    window_label: str
    total_usdc: Decimal
    payment_count: int
    avg_rate: Decimal
    min_payment_usdc: Decimal
    max_payment_usdc: Decimal
    net_positive: bool


@dataclass(slots=True)
class FundingReporter:
    """Accumulates funding payments and produces aggregated reports.

    Maintains a rolling buffer of payments; older payments are pruned
    beyond the max_history window.
    """

    portfolio_target: PortfolioTarget
    max_history_hours: int = 168  # 7 days
    _payments: list[FundingPayment] = field(default_factory=list)

    def record_payment(self, payment: FundingPayment) -> None:
        """Add a payment and prune old entries."""
        self._payments.append(payment)
        self._prune()

    def _prune(self) -> None:
        if not self._payments:
            return
        latest = self._payments[-1].timestamp
        cutoff = latest - timedelta(hours=self.max_history_hours)
        self._payments = [p for p in self._payments if p.timestamp >= cutoff]

    @property
    def payment_count(self) -> int:
        return len(self._payments)

    def _windowed(self, hours: int) -> list[FundingPayment]:
        """Return payments within the last N hours from most recent."""
        if not self._payments:
            return []
        latest = self._payments[-1].timestamp
        cutoff = latest - timedelta(hours=hours)
        return [p for p in self._payments if p.timestamp >= cutoff]

    def hourly_summary(self) -> FundingSummary:
        """Summary of the last 1 hour of funding."""
        return self._build_summary(self._windowed(1), "1h")

    def daily_summary(self) -> FundingSummary:
        """Summary of the last 24 hours of funding."""
        return self._build_summary(self._windowed(24), "24h")

    def weekly_summary(self) -> FundingSummary:
        """Summary of the last 7 days of funding."""
        return self._build_summary(self._windowed(168), "7d")

    def _build_summary(
        self,
        payments: list[FundingPayment],
        label: str,
    ) -> FundingSummary:
        if not payments:
            return FundingSummary(
                portfolio_target=self.portfolio_target,
                window_label=label,
                total_usdc=Decimal("0"),
                payment_count=0,
                avg_rate=Decimal("0"),
                min_payment_usdc=Decimal("0"),
                max_payment_usdc=Decimal("0"),
                net_positive=False,
            )

        total = sum((p.payment_usdc for p in payments), Decimal("0"))
        rates = [p.rate for p in payments]
        amounts = [p.payment_usdc for p in payments]

        return FundingSummary(
            portfolio_target=self.portfolio_target,
            window_label=label,
            total_usdc=total,
            payment_count=len(payments),
            avg_rate=sum(rates, Decimal("0")) / len(rates),
            min_payment_usdc=min(amounts),
            max_payment_usdc=max(amounts),
            net_positive=total > 0,
        )

    def entries(self, hours: int = 24) -> list[FundingReportEntry]:
        """Return individual payment entries for the time window."""
        return [
            FundingReportEntry(
                timestamp=p.timestamp,
                rate=p.rate,
                payment_usdc=p.payment_usdc,
                position_size=p.position_size,
            )
            for p in self._windowed(hours)
        ]


@dataclass(slots=True)
class DualFundingReporter:
    """Manages independent funding reporters for both portfolios."""

    reporter_a: FundingReporter = field(
        default_factory=lambda: FundingReporter(portfolio_target=PortfolioTarget.A),
    )
    reporter_b: FundingReporter = field(
        default_factory=lambda: FundingReporter(portfolio_target=PortfolioTarget.B),
    )

    def get_reporter(self, target: PortfolioTarget) -> FundingReporter:
        return self.reporter_a if target == PortfolioTarget.A else self.reporter_b

    @property
    def combined_daily_usdc(self) -> Decimal:
        return (
            self.reporter_a.daily_summary().total_usdc
            + self.reporter_b.daily_summary().total_usdc
        )
