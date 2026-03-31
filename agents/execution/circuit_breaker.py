"""Per-portfolio execution circuit breaker.

Halts order placement when adverse conditions are detected.
Each portfolio has independent circuit breaker state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from libs.common.models.enums import Route
from libs.common.utils import utc_now

# Default cooldown before auto-recovery (None = manual reset only)
DEFAULT_COOLDOWN: timedelta | None = None


@dataclass(slots=True)
class TripEvent:
    """Record of a circuit breaker trip."""

    route: Route
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

    _trips: dict[Route, TripEvent] = field(default_factory=dict)
    # Consecutive rejection counts for auto-trip
    _rejection_counts: dict[Route, int] = field(default_factory=dict)
    # Number of consecutive rejections before auto-tripping
    auto_trip_threshold: int = 5
    auto_trip_cooldown: timedelta = timedelta(minutes=5)

    def trip(
        self,
        route: Route,
        reason: str,
        now: datetime | None = None,
        cooldown: timedelta | None = DEFAULT_COOLDOWN,
    ) -> TripEvent:
        """Trip the circuit breaker for a portfolio.

        Args:
            route: Which route to halt.
            reason: Human-readable reason for the trip.
            now: Current time (defaults to utc_now).
            cooldown: Optional auto-recovery duration. None = manual reset only.

        Returns:
            The TripEvent created.
        """
        now = now or utc_now()
        event = TripEvent(
            route=route,
            reason=reason,
            tripped_at=now,
            cooldown=cooldown,
        )
        self._trips[route] = event
        return event

    def record_rejection(self, route: Route) -> TripEvent | None:
        """Record a consecutive order rejection. Auto-trips after threshold.

        Returns the TripEvent if the breaker was tripped, else None.
        """
        count = self._rejection_counts.get(route, 0) + 1
        self._rejection_counts[route] = count
        if count >= self.auto_trip_threshold:
            self._rejection_counts[route] = 0
            return self.trip(
                route,
                reason=f"auto-tripped after {count} consecutive rejections",
                cooldown=self.auto_trip_cooldown,
            )
        return None

    def record_success(self, route: Route) -> None:
        """Reset rejection counter on successful order placement."""
        self._rejection_counts.pop(route, None)

    def reset(self, route: Route) -> bool:
        """Reset the circuit breaker for a portfolio.

        Returns True if it was previously tripped.
        """
        return self._trips.pop(route, None) is not None

    def is_open(self, route: Route) -> bool:
        """Check if the circuit breaker is open (execution blocked).

        If a cooldown was set and has expired, the breaker auto-resets.
        Returns True if execution should be blocked.
        """
        trip = self._trips.get(route)
        if trip is None:
            return False
        # Auto-recover after cooldown
        if trip.cooldown is not None:
            elapsed = utc_now() - trip.tripped_at
            if elapsed >= trip.cooldown:
                self._trips.pop(route, None)
                return False
        return True

    def get_trip(self, route: Route) -> TripEvent | None:
        """Get the current trip event for a portfolio, if any."""
        return self._trips.get(route)

    @property
    def all_trips(self) -> list[TripEvent]:
        """All currently active circuit breaker trips."""
        return list(self._trips.values())

    @property
    def is_any_open(self) -> bool:
        """True if any portfolio has an open circuit breaker."""
        return len(self._trips) > 0
