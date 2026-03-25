"""Per-portfolio execution circuit breaker.

Halts order placement when adverse conditions are detected.
Each portfolio has independent circuit breaker state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from libs.common.models.enums import PortfolioTarget
from libs.common.utils import utc_now

# Default cooldown before auto-recovery (None = manual reset only)
DEFAULT_COOLDOWN: timedelta | None = None


@dataclass(slots=True)
class TripEvent:
    """Record of a circuit breaker trip."""

    portfolio_target: PortfolioTarget
    reason: str
    tripped_at: datetime
    cooldown: timedelta | None = None


@dataclass
class CircuitBreaker:
    """Per-portfolio circuit breaker for execution.

    When tripped, all order placement for the affected portfolio is blocked
    until manually reset (via Telegram /resume command) or until the optional
    cooldown period expires.

    State is in-memory only — trips are lost on restart, which is the safe
    default (a restart implies operator intervention).
    """

    _trips: dict[PortfolioTarget, TripEvent] = field(default_factory=dict)
    # Consecutive rejection counts for auto-trip
    _rejection_counts: dict[PortfolioTarget, int] = field(default_factory=dict)
    # Number of consecutive rejections before auto-tripping
    auto_trip_threshold: int = 5
    auto_trip_cooldown: timedelta = timedelta(minutes=5)

    def trip(
        self,
        portfolio_target: PortfolioTarget,
        reason: str,
        now: datetime | None = None,
        cooldown: timedelta | None = DEFAULT_COOLDOWN,
    ) -> TripEvent:
        """Trip the circuit breaker for a portfolio.

        Args:
            portfolio_target: Which portfolio to halt.
            reason: Human-readable reason for the trip.
            now: Current time (defaults to utc_now).
            cooldown: Optional auto-recovery duration. None = manual reset only.

        Returns:
            The TripEvent created.
        """
        now = now or utc_now()
        event = TripEvent(
            portfolio_target=portfolio_target,
            reason=reason,
            tripped_at=now,
            cooldown=cooldown,
        )
        self._trips[portfolio_target] = event
        return event

    def record_rejection(self, portfolio_target: PortfolioTarget) -> TripEvent | None:
        """Record a consecutive order rejection. Auto-trips after threshold.

        Returns the TripEvent if the breaker was tripped, else None.
        """
        count = self._rejection_counts.get(portfolio_target, 0) + 1
        self._rejection_counts[portfolio_target] = count
        if count >= self.auto_trip_threshold:
            self._rejection_counts[portfolio_target] = 0
            return self.trip(
                portfolio_target,
                reason=f"auto-tripped after {count} consecutive rejections",
                cooldown=self.auto_trip_cooldown,
            )
        return None

    def record_success(self, portfolio_target: PortfolioTarget) -> None:
        """Reset rejection counter on successful order placement."""
        self._rejection_counts.pop(portfolio_target, None)

    def reset(self, portfolio_target: PortfolioTarget) -> bool:
        """Reset the circuit breaker for a portfolio.

        Returns True if it was previously tripped.
        """
        return self._trips.pop(portfolio_target, None) is not None

    def is_open(self, portfolio_target: PortfolioTarget) -> bool:
        """Check if the circuit breaker is open (execution blocked).

        If a cooldown was set and has expired, the breaker auto-resets.
        Returns True if execution should be blocked.
        """
        trip = self._trips.get(portfolio_target)
        if trip is None:
            return False
        # Auto-recover after cooldown
        if trip.cooldown is not None:
            elapsed = utc_now() - trip.tripped_at
            if elapsed >= trip.cooldown:
                self._trips.pop(portfolio_target, None)
                return False
        return True

    def get_trip(self, portfolio_target: PortfolioTarget) -> TripEvent | None:
        """Get the current trip event for a portfolio, if any."""
        return self._trips.get(portfolio_target)

    @property
    def all_trips(self) -> list[TripEvent]:
        """All currently active circuit breaker trips."""
        return list(self._trips.values())

    @property
    def is_any_open(self) -> bool:
        """True if any portfolio has an open circuit breaker."""
        return len(self._trips) > 0
