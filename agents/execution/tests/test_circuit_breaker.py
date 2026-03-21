"""Tests for per-portfolio circuit breaker."""

from datetime import UTC, datetime

from libs.common.models.enums import PortfolioTarget

from agents.execution.circuit_breaker import CircuitBreaker

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestCircuitBreaker:
    def test_initially_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.is_open(PortfolioTarget.A) is False
        assert cb.is_open(PortfolioTarget.B) is False
        assert cb.is_any_open is False

    def test_trip_opens_for_portfolio(self) -> None:
        cb = CircuitBreaker()
        cb.trip(PortfolioTarget.A, "daily loss limit", now=T0)
        assert cb.is_open(PortfolioTarget.A) is True
        assert cb.is_open(PortfolioTarget.B) is False

    def test_trip_records_reason(self) -> None:
        cb = CircuitBreaker()
        event = cb.trip(PortfolioTarget.A, "max drawdown", now=T0)
        assert event.reason == "max drawdown"
        assert event.tripped_at == T0

    def test_reset_closes(self) -> None:
        cb = CircuitBreaker()
        cb.trip(PortfolioTarget.A, "test")
        assert cb.reset(PortfolioTarget.A) is True
        assert cb.is_open(PortfolioTarget.A) is False

    def test_reset_not_tripped_returns_false(self) -> None:
        cb = CircuitBreaker()
        assert cb.reset(PortfolioTarget.A) is False

    def test_independent_per_portfolio(self) -> None:
        cb = CircuitBreaker()
        cb.trip(PortfolioTarget.A, "A issue")
        cb.trip(PortfolioTarget.B, "B issue")
        assert cb.is_any_open is True
        cb.reset(PortfolioTarget.A)
        assert cb.is_open(PortfolioTarget.A) is False
        assert cb.is_open(PortfolioTarget.B) is True

    def test_get_trip(self) -> None:
        cb = CircuitBreaker()
        cb.trip(PortfolioTarget.B, "test reason", now=T0)
        event = cb.get_trip(PortfolioTarget.B)
        assert event is not None
        assert event.reason == "test reason"

    def test_get_trip_none_when_not_tripped(self) -> None:
        cb = CircuitBreaker()
        assert cb.get_trip(PortfolioTarget.A) is None

    def test_all_trips(self) -> None:
        cb = CircuitBreaker()
        cb.trip(PortfolioTarget.A, "reason A")
        cb.trip(PortfolioTarget.B, "reason B")
        trips = cb.all_trips
        assert len(trips) == 2
