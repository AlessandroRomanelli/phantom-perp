"""Tests for system health checking."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agents.monitoring.health_checker import (
    HealthChecker,
    check_data_freshness,
    check_liquidation_distance,
    check_margin_health,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestHealthChecker:
    def test_no_components(self) -> None:
        checker = HealthChecker()
        health = checker.check_all(T0)
        assert health.is_healthy is True
        assert health.unhealthy_count == 0
        assert len(health.components) == 0

    def test_healthy_component(self) -> None:
        checker = HealthChecker(stale_threshold=timedelta(seconds=30))
        checker.record_heartbeat("ingestion", T0)
        result = checker.check_component("ingestion", T0 + timedelta(seconds=10))
        assert result.is_healthy is True
        assert result.stale_seconds == 10.0

    def test_stale_component(self) -> None:
        checker = HealthChecker(stale_threshold=timedelta(seconds=30))
        checker.record_heartbeat("ingestion", T0)
        result = checker.check_component("ingestion", T0 + timedelta(seconds=60))
        assert result.is_healthy is False
        assert "stale" in result.detail

    def test_never_seen_component(self) -> None:
        checker = HealthChecker()
        result = checker.check_component("unknown", T0)
        assert result.is_healthy is False
        assert result.last_seen is None
        assert result.detail == "never seen"

    def test_check_all(self) -> None:
        checker = HealthChecker(stale_threshold=timedelta(seconds=30))
        checker.record_heartbeat("ingestion", T0)
        checker.record_heartbeat("signals", T0)
        checker.record_heartbeat("execution", T0 - timedelta(seconds=60))
        health = checker.check_all(T0 + timedelta(seconds=10))
        assert health.unhealthy_count == 1
        assert health.is_healthy is False
        assert len(health.components) == 3

    def test_all_healthy(self) -> None:
        checker = HealthChecker(stale_threshold=timedelta(seconds=30))
        checker.record_heartbeat("ingestion", T0)
        checker.record_heartbeat("signals", T0)
        health = checker.check_all(T0 + timedelta(seconds=5))
        assert health.is_healthy is True
        assert health.unhealthy_count == 0

    def test_registered_components(self) -> None:
        checker = HealthChecker()
        checker.record_heartbeat("c", T0)
        checker.record_heartbeat("a", T0)
        checker.record_heartbeat("b", T0)
        assert checker.registered_components == ["a", "b", "c"]


class TestCheckDataFreshness:
    def test_fresh_data(self) -> None:
        assert check_data_freshness(T0, T0 + timedelta(seconds=10)) is True

    def test_stale_data(self) -> None:
        assert check_data_freshness(T0, T0 + timedelta(seconds=60)) is False

    def test_no_data(self) -> None:
        assert check_data_freshness(None, T0) is False

    def test_custom_threshold(self) -> None:
        assert check_data_freshness(
            T0, T0 + timedelta(seconds=50), threshold_seconds=60,
        ) is True


class TestCheckMarginHealth:
    def test_healthy(self) -> None:
        assert check_margin_health(30.0, 50.0) is True

    def test_unhealthy(self) -> None:
        assert check_margin_health(55.0, 50.0) is False

    def test_at_threshold(self) -> None:
        assert check_margin_health(50.0, 50.0) is False


class TestCheckLiquidationDistance:
    def test_safe_distance(self) -> None:
        # mark=2200, liq=1890, distance = 14.09% > 8%
        assert check_liquidation_distance(
            Decimal("2200"), Decimal("1890"), 8.0,
        ) is True

    def test_close_distance(self) -> None:
        # mark=2200, liq=2100, distance = 4.55% < 8%
        assert check_liquidation_distance(
            Decimal("2200"), Decimal("2100"), 8.0,
        ) is False

    def test_zero_mark_price(self) -> None:
        assert check_liquidation_distance(
            Decimal("0"), Decimal("1890"), 8.0,
        ) is True

    def test_zero_liquidation(self) -> None:
        assert check_liquidation_distance(
            Decimal("2200"), Decimal("0"), 8.0,
        ) is True
