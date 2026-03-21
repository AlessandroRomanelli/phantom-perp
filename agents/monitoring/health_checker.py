"""System health checks — data freshness, heartbeats, connection status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.constants import STALE_DATA_HALT_SECONDS


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Health status of a single system component."""

    name: str
    is_healthy: bool
    last_seen: datetime | None
    stale_seconds: float
    detail: str


@dataclass(frozen=True, slots=True)
class SystemHealth:
    """Aggregated health status across all monitored components."""

    timestamp: datetime
    components: list[ComponentHealth]
    is_healthy: bool
    unhealthy_count: int


@dataclass(slots=True)
class HealthChecker:
    """Tracks liveness of system components via heartbeat timestamps.

    Each component registers heartbeats; the checker evaluates which
    components are stale beyond their configured thresholds.

    Components can have individual thresholds. Event-driven components
    (e.g. fills, funding) can be registered as event-only, meaning they
    record heartbeats for informational purposes but are never flagged
    as stale since they fire on external events, not on a schedule.
    """

    stale_threshold: timedelta = field(
        default_factory=lambda: timedelta(seconds=STALE_DATA_HALT_SECONDS),
    )
    _heartbeats: dict[str, datetime] = field(default_factory=dict)
    _component_thresholds: dict[str, timedelta | None] = field(default_factory=dict)

    def record_heartbeat(self, component: str, timestamp: datetime) -> None:
        """Record a heartbeat for a component."""
        self._heartbeats[component] = timestamp

    def set_threshold(self, component: str, threshold: timedelta | None) -> None:
        """Set a custom staleness threshold for a component.

        Args:
            component: Component name.
            threshold: Custom threshold, or None to mark as event-only
                       (never considered stale).
        """
        self._component_thresholds[component] = threshold

    def check_component(self, component: str, now: datetime) -> ComponentHealth:
        """Check health of a single component."""
        last_seen = self._heartbeats.get(component)
        if last_seen is None:
            return ComponentHealth(
                name=component,
                is_healthy=False,
                last_seen=None,
                stale_seconds=0.0,
                detail="never seen",
            )

        age = (now - last_seen).total_seconds()

        # Look up per-component threshold; None means event-only (always healthy)
        if component in self._component_thresholds:
            threshold = self._component_thresholds[component]
            if threshold is None:
                return ComponentHealth(
                    name=component,
                    is_healthy=True,
                    last_seen=last_seen,
                    stale_seconds=age,
                    detail="ok (event-driven)",
                )
            threshold_secs = threshold.total_seconds()
        else:
            threshold_secs = self.stale_threshold.total_seconds()

        is_healthy = age <= threshold_secs
        return ComponentHealth(
            name=component,
            is_healthy=is_healthy,
            last_seen=last_seen,
            stale_seconds=age,
            detail="ok" if is_healthy else f"stale ({age:.0f}s)",
        )

    def check_all(self, now: datetime) -> SystemHealth:
        """Check health of all registered components."""
        components = [
            self.check_component(name, now) for name in sorted(self._heartbeats)
        ]
        unhealthy = [c for c in components if not c.is_healthy]
        return SystemHealth(
            timestamp=now,
            components=components,
            is_healthy=len(unhealthy) == 0,
            unhealthy_count=len(unhealthy),
        )

    @property
    def registered_components(self) -> list[str]:
        return sorted(self._heartbeats.keys())


def check_data_freshness(
    last_market_data_time: datetime | None,
    now: datetime,
    threshold_seconds: int = STALE_DATA_HALT_SECONDS,
) -> bool:
    """Return True if market data is fresh (within threshold)."""
    if last_market_data_time is None:
        return False
    age = (now - last_market_data_time).total_seconds()
    return age <= threshold_seconds


def check_margin_health(
    margin_utilization_pct: float,
    threshold_pct: float,
) -> bool:
    """Return True if margin utilization is below the alert threshold."""
    return margin_utilization_pct < threshold_pct


def check_liquidation_distance(
    mark_price: Decimal,
    liquidation_price: Decimal,
    threshold_pct: float,
) -> bool:
    """Return True if liquidation price is far enough from mark price."""
    if mark_price <= 0 or liquidation_price <= 0:
        return True  # No position or no liquidation price
    distance = abs(mark_price - liquidation_price) / mark_price * 100
    return float(distance) >= threshold_pct
