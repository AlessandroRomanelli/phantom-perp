"""Tests for RouteRouter routing rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from libs.common.models.enums import PositionSide, Route, SignalSource
from libs.common.models.signal import StandardSignal
from libs.portfolio.router import RouteRouter

INSTRUMENTS = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]


def _signal(
    *,
    time_horizon: timedelta = timedelta(hours=1),
    conviction: float = 0.5,
    source: SignalSource = SignalSource.MOMENTUM,
    suggested_route: Route | None = None,
    instrument: str = "ETH-PERP",
) -> StandardSignal:
    return StandardSignal(
        signal_id="test-sig-001",
        timestamp=datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC),
        instrument=instrument,
        direction=PositionSide.LONG,
        conviction=conviction,
        source=source,
        time_horizon=time_horizon,
        reasoning="Test signal",
        suggested_route=suggested_route,
    )


@pytest.fixture
def router() -> RouteRouter:
    return RouteRouter()


def test_short_horizon_routes_to_a(router: RouteRouter) -> None:
    """Time horizon < 2h should route to A."""
    sig = _signal(time_horizon=timedelta(hours=1))
    assert router.route(sig) == Route.A


def test_long_horizon_routes_to_b(router: RouteRouter) -> None:
    """Time horizon 6h with moderate conviction should route to B."""
    sig = _signal(time_horizon=timedelta(hours=6), conviction=0.5)
    assert router.route(sig) == Route.B


def test_high_conviction_medium_horizon_routes_to_a(router: RouteRouter) -> None:
    """High conviction (0.90) + medium horizon (3h < 4h) should route to A."""
    sig = _signal(conviction=0.90, time_horizon=timedelta(hours=3))
    assert router.route(sig) == Route.A


def test_high_conviction_long_horizon_routes_to_b(router: RouteRouter) -> None:
    """High conviction (0.90) + long horizon (5h >= 4h) should route to B."""
    sig = _signal(conviction=0.90, time_horizon=timedelta(hours=5))
    assert router.route(sig) == Route.B


def test_default_fallback_routes_to_b(router: RouteRouter) -> None:
    """Medium conviction + medium horizon (not matching any A rule) falls back to B."""
    sig = _signal(conviction=0.6, time_horizon=timedelta(hours=3))
    assert router.route(sig) == Route.B


def test_suggested_route_a_honored(router: RouteRouter) -> None:
    """Explicit suggested_route=A overrides all other rules."""
    sig = _signal(
        suggested_route=Route.A,
        time_horizon=timedelta(hours=24),
        conviction=0.1,
    )
    assert router.route(sig) == Route.A


def test_suggested_route_b_honored(router: RouteRouter) -> None:
    """Explicit suggested_route=B overrides all other rules."""
    sig = _signal(
        suggested_route=Route.B,
        time_horizon=timedelta(minutes=30),
        conviction=1.0,
    )
    assert router.route(sig) == Route.B


def test_route_with_reason_short_horizon(router: RouteRouter) -> None:
    """Short horizon reason string should mention 'Short time horizon'."""
    sig = _signal(time_horizon=timedelta(hours=1))
    route, reason = router.route_with_reason(sig)
    assert route == Route.A
    assert "Short time horizon" in reason


def test_route_with_reason_high_conviction(router: RouteRouter) -> None:
    """High conviction reason string should mention 'High conviction' and the value."""
    sig = _signal(conviction=0.90, time_horizon=timedelta(hours=3))
    route, reason = router.route_with_reason(sig)
    assert route == Route.A
    assert "High conviction" in reason
    assert "0.90" in reason


def test_route_with_reason_default(router: RouteRouter) -> None:
    """Default fallback reason string should mention 'Default routing'."""
    sig = _signal(conviction=0.6, time_horizon=timedelta(hours=3))
    route, reason = router.route_with_reason(sig)
    assert route == Route.B
    assert "Default routing" in reason


def test_route_with_reason_suggested(router: RouteRouter) -> None:
    """Suggested route reason string should mention 'Strategy suggested route'."""
    sig = _signal(suggested_route=Route.A)
    route, reason = router.route_with_reason(sig)
    assert route == Route.A
    assert "Strategy suggested route" in reason


def test_all_instruments_routable(router: RouteRouter) -> None:
    """Every supported instrument should be routable without error."""
    for instrument in INSTRUMENTS:
        sig = _signal(instrument=instrument)
        result = router.route(sig)
        assert isinstance(result, Route)


def test_both_routes_covered(router: RouteRouter) -> None:
    """Verify that both Route.A and Route.B are reachable."""
    short = _signal(time_horizon=timedelta(hours=1))
    long = _signal(time_horizon=timedelta(hours=6), conviction=0.5)
    routes = {router.route(short), router.route(long)}
    assert routes == {Route.A, Route.B}
