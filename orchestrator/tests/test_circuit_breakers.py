"""Tests for system-level circuit breakers and kill switches."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import Route

from orchestrator.circuit_breakers import (
    KillSwitchReason,
    SystemCircuitBreaker,
    evaluate_daily_loss,
    evaluate_drawdown,
    evaluate_funding_rate,
    evaluate_stale_data,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestSystemCircuitBreaker:
    def test_initially_not_halted(self) -> None:
        cb = SystemCircuitBreaker()
        assert cb.is_halted(Route.A) is False
        assert cb.is_halted(Route.B) is False
        assert cb.is_globally_halted() is False

    def test_trip_route_a(self) -> None:
        cb = SystemCircuitBreaker()
        event = cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS,
            "Daily loss exceeded", T0,
        )
        assert cb.is_halted(Route.A) is True
        assert cb.is_halted(Route.B) is False
        assert event.route == Route.A

    def test_trip_route_b(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.B, KillSwitchReason.MAX_DRAWDOWN,
            "Drawdown exceeded", T0,
        )
        assert cb.is_halted(Route.B) is True
        assert cb.is_halted(Route.A) is False

    def test_global_trip_halts_both(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_global(KillSwitchReason.STALE_DATA, "Data stale", T0)
        assert cb.is_halted(Route.A) is True
        assert cb.is_halted(Route.B) is True
        assert cb.is_globally_halted() is True

    def test_emergency_kill(self) -> None:
        cb = SystemCircuitBreaker()
        event = cb.emergency_kill(T0)
        assert event.reason == KillSwitchReason.EMERGENCY
        assert cb.is_globally_halted() is True

    def test_reset_portfolio(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS,
            "test", T0,
        )
        assert cb.reset_portfolio(Route.A) is True
        assert cb.is_halted(Route.A) is False

    def test_reset_not_tripped(self) -> None:
        cb = SystemCircuitBreaker()
        assert cb.reset_portfolio(Route.A) is False

    def test_reset_global(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_global(KillSwitchReason.STALE_DATA, "test", T0)
        assert cb.reset_global() is True
        assert cb.is_globally_halted() is False

    def test_reset_all(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS, "test", T0,
        )
        cb.trip_global(KillSwitchReason.STALE_DATA, "test", T0)
        cb.reset_all()
        assert cb.any_halted is False

    def test_active_trips(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS, "a", T0,
        )
        cb.trip_portfolio(
            Route.B, KillSwitchReason.MAX_DRAWDOWN, "b", T0,
        )
        assert len(cb.active_trips) == 2

    def test_active_trips_with_global(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS, "a", T0,
        )
        cb.trip_global(KillSwitchReason.STALE_DATA, "global", T0)
        assert len(cb.active_trips) == 2

    def test_get_trip(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS,
            "test", T0, value="12.0", threshold="10.0",
        )
        trip = cb.get_trip(Route.A)
        assert trip is not None
        assert trip.value == "12.0"
        assert trip.threshold == "10.0"

    def test_portfolio_halted_by_global_but_no_portfolio_trip(self) -> None:
        cb = SystemCircuitBreaker()
        cb.trip_global(KillSwitchReason.STALE_DATA, "test", T0)
        assert cb.is_halted(Route.A) is True
        assert cb.get_trip(Route.A) is None  # no portfolio-specific trip


class TestEvaluateDailyLoss:
    def test_route_a_below_threshold(self) -> None:
        event = evaluate_daily_loss(5.0, Route.A, T0)
        assert event is None  # A's threshold is 10%

    def test_route_a_at_threshold(self) -> None:
        event = evaluate_daily_loss(10.0, Route.A, T0)
        assert event is not None
        assert event.reason == KillSwitchReason.DAILY_LOSS
        assert event.route == Route.A

    def test_route_a_above_threshold(self) -> None:
        event = evaluate_daily_loss(12.0, Route.A, T0)
        assert event is not None

    def test_route_b_below_threshold(self) -> None:
        event = evaluate_daily_loss(3.0, Route.B, T0)
        assert event is None  # B's threshold is 5%

    def test_route_b_at_threshold(self) -> None:
        event = evaluate_daily_loss(5.0, Route.B, T0)
        assert event is not None
        assert event.route == Route.B

    def test_route_b_stricter_than_a(self) -> None:
        # 7% loss: kills B but not A
        assert evaluate_daily_loss(7.0, Route.A, T0) is None
        assert evaluate_daily_loss(7.0, Route.B, T0) is not None


class TestEvaluateDrawdown:
    def test_route_a_below_threshold(self) -> None:
        assert evaluate_drawdown(20.0, Route.A, T0) is None

    def test_route_a_at_threshold(self) -> None:
        event = evaluate_drawdown(25.0, Route.A, T0)
        assert event is not None
        assert event.reason == KillSwitchReason.MAX_DRAWDOWN

    def test_route_b_below_threshold(self) -> None:
        assert evaluate_drawdown(10.0, Route.B, T0) is None

    def test_route_b_at_threshold(self) -> None:
        event = evaluate_drawdown(15.0, Route.B, T0)
        assert event is not None

    def test_route_b_stricter_than_a(self) -> None:
        # 20% drawdown: kills B but not A
        assert evaluate_drawdown(20.0, Route.A, T0) is None
        assert evaluate_drawdown(20.0, Route.B, T0) is not None


class TestEvaluateStaleData:
    def test_fresh_data(self) -> None:
        assert evaluate_stale_data(10.0, T0) is None

    def test_at_threshold(self) -> None:
        assert evaluate_stale_data(30.0, T0) is None  # not stale at exactly 30

    def test_stale_data(self) -> None:
        event = evaluate_stale_data(45.0, T0)
        assert event is not None
        assert event.reason == KillSwitchReason.STALE_DATA
        assert event.route is None  # global

    def test_message_includes_seconds(self) -> None:
        event = evaluate_stale_data(60.0, T0)
        assert event is not None
        assert "60" in event.message


class TestEvaluateFundingRate:
    def test_normal_rate(self) -> None:
        assert evaluate_funding_rate(Decimal("0.0001"), T0) is None

    def test_at_threshold(self) -> None:
        event = evaluate_funding_rate(Decimal("0.0005"), T0)
        assert event is not None
        assert event.reason == KillSwitchReason.FUNDING_RATE

    def test_above_threshold(self) -> None:
        event = evaluate_funding_rate(Decimal("0.001"), T0)
        assert event is not None

    def test_negative_rate_above_threshold(self) -> None:
        event = evaluate_funding_rate(Decimal("-0.0006"), T0)
        assert event is not None
        assert "negative" in event.message

    def test_global_halt(self) -> None:
        event = evaluate_funding_rate(Decimal("0.0005"), T0)
        assert event is not None
        assert event.route is None  # global
