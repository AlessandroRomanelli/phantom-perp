"""Per-portfolio execution circuit breaker.

Halts order placement when adverse conditions are detected.
Each portfolio has independent circuit breaker state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from libs.common.models.enums import PortfolioTarget
from libs.common.utils import utc_now


@dataclass(slots=True)
class TripEvent:
    """Record of a circuit breaker trip."""

    portfolio_target: PortfolioTarget
    reason: str
    tripped_at: datetime


@dataclass
class CircuitBreaker:
    """Per-portfolio circuit breaker for execution.

    When tripped, all order placement for the affected portfolio is blocked
    until manually reset (via Telegram /resume command).
    """

    _trips: dict[PortfolioTarget, TripEvent] = field(default_factory=dict)

    def trip(
        self,
        portfolio_target: PortfolioTarget,
        reason: str,
        now: datetime | None = None,
    ) -> TripEvent:
        """Trip the circuit breaker for a portfolio.

        Args:
            portfolio_target: Which portfolio to halt.
            reason: Human-readable reason for the trip.
            now: Current time (defaults to utc_now).

        Returns:
            The TripEvent created.
        """
        now = now or utc_now()
        event = TripEvent(
            portfolio_target=portfolio_target,
            reason=reason,
            tripped_at=now,
        )
        self._trips[portfolio_target] = event
        return event

    def reset(self, portfolio_target: PortfolioTarget) -> bool:
        """Reset the circuit breaker for a portfolio.

        Returns True if it was previously tripped.
        """
        return self._trips.pop(portfolio_target, None) is not None

    def is_open(self, portfolio_target: PortfolioTarget) -> bool:
        """Check if the circuit breaker is open (execution blocked).

        Returns True if execution should be blocked.
        """
        return portfolio_target in self._trips

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
