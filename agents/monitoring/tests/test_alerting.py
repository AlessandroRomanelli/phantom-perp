"""Tests for alert generation."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget

from agents.monitoring.alerting import (
    AlertSeverity,
    AlertType,
    check_daily_loss,
    check_drawdown,
    check_funding_rate,
    check_liquidation_proximity,
    check_margin_utilization,
    check_opposing_positions,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestCheckFundingRate:
    def test_no_alert_below_threshold(self) -> None:
        alert = check_funding_rate(
            Decimal("0.0001"), 0.03, PortfolioTarget.A, T0,
        )
        assert alert is None

    def test_alert_above_threshold(self) -> None:
        # 0.0005 = 0.05% > 0.03%
        alert = check_funding_rate(
            Decimal("0.0005"), 0.03, PortfolioTarget.A, T0,
        )
        assert alert is not None
        assert alert.alert_type == AlertType.FUNDING_RATE_HIGH
        assert alert.severity == AlertSeverity.WARNING
        assert "positive (longs pay)" in alert.message

    def test_negative_rate_alert(self) -> None:
        alert = check_funding_rate(
            Decimal("-0.0005"), 0.03, PortfolioTarget.A, T0,
        )
        assert alert is not None
        assert "negative (shorts pay)" in alert.message

    def test_at_threshold(self) -> None:
        alert = check_funding_rate(
            Decimal("0.0003"), 0.03, PortfolioTarget.A, T0,
        )
        assert alert is not None


class TestCheckMarginUtilization:
    def test_no_alert_below_threshold(self) -> None:
        alert = check_margin_utilization(30.0, 50.0, PortfolioTarget.A, T0)
        assert alert is None

    def test_alert_above_threshold(self) -> None:
        alert = check_margin_utilization(55.0, 50.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.alert_type == AlertType.MARGIN_HIGH
        assert "55.0%" in alert.message


class TestCheckLiquidationProximity:
    def test_no_alert_safe_distance(self) -> None:
        alert = check_liquidation_proximity(
            Decimal("2200"), Decimal("1890"), 8.0, PortfolioTarget.A, T0,
        )
        assert alert is None

    def test_alert_close_distance(self) -> None:
        # mark=2200, liq=2100, distance=4.55% < 8%
        alert = check_liquidation_proximity(
            Decimal("2200"), Decimal("2100"), 8.0, PortfolioTarget.A, T0,
        )
        assert alert is not None
        assert alert.alert_type == AlertType.LIQUIDATION_CLOSE
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_zero_prices(self) -> None:
        alert = check_liquidation_proximity(
            Decimal("0"), Decimal("1890"), 8.0, PortfolioTarget.A, T0,
        )
        assert alert is None


class TestCheckDrawdown:
    def test_no_alert_below_threshold(self) -> None:
        # 10% dd, kill at 25%, warn at 20%
        alert = check_drawdown(10.0, 25.0, PortfolioTarget.A, T0)
        assert alert is None

    def test_warning_at_80pct(self) -> None:
        # 20% dd = 80% of 25% kill switch
        alert = check_drawdown(20.0, 25.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_critical_at_kill_switch(self) -> None:
        alert = check_drawdown(25.0, 25.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_critical_above_kill_switch(self) -> None:
        alert = check_drawdown(30.0, 25.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL


class TestCheckDailyLoss:
    def test_no_alert_below_threshold(self) -> None:
        alert = check_daily_loss(3.0, 10.0, PortfolioTarget.A, T0)
        assert alert is None

    def test_warning_approaching(self) -> None:
        # 8% = 80% of 10% kill switch
        alert = check_daily_loss(8.0, 10.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING
        assert alert.alert_type == AlertType.DAILY_LOSS_WARNING

    def test_critical_at_kill_switch(self) -> None:
        alert = check_daily_loss(10.0, 10.0, PortfolioTarget.A, T0)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL


class TestCheckOpposingPositions:
    def test_no_alert_same_side(self) -> None:
        alert = check_opposing_positions("LONG", "LONG", T0)
        assert alert is None

    def test_alert_opposing(self) -> None:
        alert = check_opposing_positions("LONG", "SHORT", T0)
        assert alert is not None
        assert alert.alert_type == AlertType.OPPOSING_POSITIONS
        assert alert.severity == AlertSeverity.INFO
        assert alert.portfolio_target is None

    def test_no_alert_one_side_none(self) -> None:
        alert = check_opposing_positions("LONG", None, T0)
        assert alert is None

    def test_no_alert_both_none(self) -> None:
        alert = check_opposing_positions(None, None, T0)
        assert alert is None
