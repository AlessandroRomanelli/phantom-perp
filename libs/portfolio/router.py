"""Route routing — assigns signals to Route A or B based on configurable rules."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from libs.common.models.enums import Route, SignalSource
from libs.common.models.signal import StandardSignal


# Historical note: _SHORT_HORIZON_SOURCES previously forced FUNDING_ARB,
# ORDERBOOK_IMBALANCE, and LIQUIDATION_CASCADE to Route A regardless of
# conviction.  Removed to let each strategy control its own routing via
# suggested_route and route_a_min_conviction in YAML.
_SHORT_HORIZON_SOURCES: frozenset[SignalSource] = frozenset()

_DEFAULT_SHORT_HORIZON_THRESHOLD = timedelta(hours=2)
_HIGH_CONVICTION_SHORT_HORIZON_THRESHOLD = timedelta(hours=4)
_HIGH_CONVICTION_MIN = 0.85


class RouteRouter:
    """Route signals to Route A or B based on configurable rules.

    Rules are evaluated in order. First match wins.
    Default behavior (no config) routes short-horizon and high-frequency
    strategies to A, everything else to B.

    Args:
        config: Routing configuration from YAML. If None, uses defaults.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._rules = self._config.get("rules", [])

    def route(self, signal: StandardSignal) -> Route:
        """Determine which route a signal should be sent to.

        Args:
            signal: The trading signal to route.

        Returns:
            Route.A for autonomous execution,
            Route.B for user-confirmed execution.
        """
        # If the strategy explicitly suggests a route, honor it.
        # This respects per-strategy conviction thresholds: a strategy that
        # sets suggested_route=B is saying "my conviction is too low for A".
        if signal.suggested_route is not None:
            return signal.suggested_route

        # Rule 1: Short time horizon → A
        if signal.time_horizon < _DEFAULT_SHORT_HORIZON_THRESHOLD:
            return Route.A

        # Rule 2: High-frequency strategies → A
        if signal.source in _SHORT_HORIZON_SOURCES:
            return Route.A

        # Rule 3: High conviction + medium-short horizon → A
        if (
            signal.conviction >= _HIGH_CONVICTION_MIN
            and signal.time_horizon < _HIGH_CONVICTION_SHORT_HORIZON_THRESHOLD
        ):
            return Route.A

        # Default: → B (user-confirmed)
        return Route.B

    def route_with_reason(self, signal: StandardSignal) -> tuple[Route, str]:
        """Route a signal and return the reason for the routing decision.

        Args:
            signal: The trading signal to route.

        Returns:
            Tuple of (route, human-readable reason).
        """
        if signal.suggested_route is not None:
            label = "A" if signal.suggested_route == Route.A else "B"
            return signal.suggested_route, f"Strategy suggested route ({label})"

        if signal.time_horizon < _DEFAULT_SHORT_HORIZON_THRESHOLD:
            return Route.A, f"Short time horizon ({signal.time_horizon})"

        if signal.source in _SHORT_HORIZON_SOURCES:
            return Route.A, f"High-frequency strategy ({signal.source.value})"

        if (
            signal.conviction >= _HIGH_CONVICTION_MIN
            and signal.time_horizon < _HIGH_CONVICTION_SHORT_HORIZON_THRESHOLD
        ):
            return (
                Route.A,
                f"High conviction ({signal.conviction:.2f}) + medium horizon",
            )

        return Route.B, "Default routing (longer horizon / lower conviction)"
