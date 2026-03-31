"""Track hourly funding payments per portfolio.

With 24 settlements per day, funding accumulates quickly.
This tracker maintains a rolling window of payments and computes
daily aggregates for each portfolio independently.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import Route, PositionSide
from libs.common.models.funding import FundingPayment


@dataclass
class FundingTracker:
    """Tracks hourly funding payments for a single portfolio."""

    route: Route
    window_hours: int = 24
    _payments: deque[FundingPayment] = field(default_factory=deque)

    def record_payment(self, payment: FundingPayment) -> None:
        """Record a new hourly funding settlement."""
        self._payments.append(payment)
        self._prune()

    def compute_payment(
        self,
        rate: Decimal,
        position_size: Decimal,
        position_side: PositionSide,
        mark_price: Decimal,
        instrument: str,
        timestamp: datetime,
    ) -> FundingPayment:
        """Compute a funding payment from current position and rate.

        When funding rate is positive:
          - Longs pay: payment = -(rate * notional)
          - Shorts receive: payment = +(rate * notional)
        When funding rate is negative:
          - Longs receive: payment = +(|rate| * notional)
          - Shorts pay: payment = -(|rate| * notional)
        """
        notional = position_size * mark_price

        if position_side == PositionSide.LONG:
            # Longs pay when rate > 0, receive when rate < 0
            payment_usdc = -(rate * notional)
        else:
            # Shorts receive when rate > 0, pay when rate < 0
            payment_usdc = rate * notional

        cumulative = self.cumulative_24h_usdc + payment_usdc

        fp = FundingPayment(
            timestamp=timestamp,
            instrument=instrument,
            route=self.route,
            rate=rate,
            payment_usdc=payment_usdc,
            position_size=position_size,
            position_side=position_side,
            cumulative_24h_usdc=cumulative,
        )
        self.record_payment(fp)
        return fp

    @property
    def cumulative_24h_usdc(self) -> Decimal:
        """Total funding paid/received in the last 24 hours."""
        return sum((p.payment_usdc for p in self._payments), Decimal("0"))

    @property
    def payment_count(self) -> int:
        """Number of settlements in the window."""
        return len(self._payments)

    @property
    def payments(self) -> list[FundingPayment]:
        """All payments in the current window."""
        return list(self._payments)

    @property
    def net_positive(self) -> bool:
        """True if we've received more funding than paid (net positive)."""
        return self.cumulative_24h_usdc > 0

    def _prune(self) -> None:
        """Remove payments older than the window."""
        if not self._payments:
            return
        cutoff = self._payments[-1].timestamp - timedelta(hours=self.window_hours)
        while self._payments and self._payments[0].timestamp < cutoff:
            self._payments.popleft()


@dataclass
class DualFundingTracker:
    """Manages funding trackers for both portfolios."""

    tracker_a: FundingTracker = field(
        default_factory=lambda: FundingTracker(route=Route.A),
    )
    tracker_b: FundingTracker = field(
        default_factory=lambda: FundingTracker(route=Route.B),
    )

    def get_tracker(self, target: Route) -> FundingTracker:
        return self.tracker_a if target == Route.A else self.tracker_b

    @property
    def combined_24h_usdc(self) -> Decimal:
        return self.tracker_a.cumulative_24h_usdc + self.tracker_b.cumulative_24h_usdc
