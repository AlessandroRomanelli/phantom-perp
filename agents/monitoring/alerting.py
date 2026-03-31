"""Alert generation based on monitoring thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from libs.common.models.enums import Route


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    FUNDING_RATE_HIGH = "funding_rate_high"
    MARGIN_HIGH = "margin_high"
    LIQUIDATION_CLOSE = "liquidation_close"
    DATA_STALE = "data_stale"
    COMPONENT_DOWN = "component_down"
    DRAWDOWN_WARNING = "drawdown_warning"
    DAILY_LOSS_WARNING = "daily_loss_warning"
    OPPOSING_POSITIONS = "opposing_positions"


@dataclass(frozen=True, slots=True)
class Alert:
    """A monitoring alert to be published to stream:alerts."""

    alert_type: AlertType
    severity: AlertSeverity
    route: Route | None
    message: str
    timestamp: datetime
    value: float | None = None
    threshold: float | None = None


def check_funding_rate(
    rate: Decimal,
    threshold_pct: float,
    route: Route,
    now: datetime,
) -> Alert | None:
    """Alert if absolute hourly funding rate exceeds threshold."""
    abs_rate_pct = float(abs(rate)) * 100
    if abs_rate_pct >= threshold_pct:
        direction = "positive (longs pay)" if rate > 0 else "negative (shorts pay)"
        return Alert(
            alert_type=AlertType.FUNDING_RATE_HIGH,
            severity=AlertSeverity.WARNING,
            route=route,
            message=f"Funding rate {direction}: {abs_rate_pct:.4f}% (threshold: {threshold_pct}%)",
            timestamp=now,
            value=abs_rate_pct,
            threshold=threshold_pct,
        )
    return None


def check_margin_utilization(
    utilization_pct: float,
    threshold_pct: float,
    route: Route,
    now: datetime,
) -> Alert | None:
    """Alert if margin utilization exceeds threshold."""
    if utilization_pct >= threshold_pct:
        return Alert(
            alert_type=AlertType.MARGIN_HIGH,
            severity=AlertSeverity.WARNING,
            route=route,
            message=f"Margin utilization {utilization_pct:.1f}% (threshold: {threshold_pct}%)",
            timestamp=now,
            value=utilization_pct,
            threshold=threshold_pct,
        )
    return None


def check_liquidation_proximity(
    mark_price: Decimal,
    liquidation_price: Decimal,
    threshold_pct: float,
    route: Route,
    now: datetime,
) -> Alert | None:
    """Alert if liquidation price is too close to mark price."""
    if mark_price <= 0 or liquidation_price <= 0:
        return None
    distance_pct = float(abs(mark_price - liquidation_price) / mark_price) * 100
    if distance_pct < threshold_pct:
        return Alert(
            alert_type=AlertType.LIQUIDATION_CLOSE,
            severity=AlertSeverity.CRITICAL,
            route=route,
            message=(
                f"Liquidation {distance_pct:.1f}% away "
                f"(mark: {mark_price}, liq: {liquidation_price}, threshold: {threshold_pct}%)"
            ),
            timestamp=now,
            value=distance_pct,
            threshold=threshold_pct,
        )
    return None


def check_drawdown(
    current_drawdown_pct: float,
    max_drawdown_pct: float,
    route: Route,
    now: datetime,
) -> Alert | None:
    """Alert when drawdown approaches the kill-switch threshold."""
    # Warn at 80% of the kill switch level
    warn_threshold = max_drawdown_pct * 0.8
    if current_drawdown_pct >= warn_threshold:
        severity = (
            AlertSeverity.CRITICAL
            if current_drawdown_pct >= max_drawdown_pct
            else AlertSeverity.WARNING
        )
        return Alert(
            alert_type=AlertType.DRAWDOWN_WARNING,
            severity=severity,
            route=route,
            message=(
                f"Drawdown {current_drawdown_pct:.1f}% "
                f"(kill switch: {max_drawdown_pct}%)"
            ),
            timestamp=now,
            value=current_drawdown_pct,
            threshold=max_drawdown_pct,
        )
    return None


def check_daily_loss(
    daily_loss_pct: float,
    max_daily_loss_pct: float,
    route: Route,
    now: datetime,
) -> Alert | None:
    """Alert when daily loss approaches the kill-switch threshold."""
    warn_threshold = max_daily_loss_pct * 0.8
    if daily_loss_pct >= warn_threshold:
        severity = (
            AlertSeverity.CRITICAL
            if daily_loss_pct >= max_daily_loss_pct
            else AlertSeverity.WARNING
        )
        return Alert(
            alert_type=AlertType.DAILY_LOSS_WARNING,
            severity=severity,
            route=route,
            message=(
                f"Daily loss {daily_loss_pct:.1f}% "
                f"(kill switch: {max_daily_loss_pct}%)"
            ),
            timestamp=now,
            value=daily_loss_pct,
            threshold=max_daily_loss_pct,
        )
    return None


def check_opposing_positions(
    a_side: str | None,
    b_side: str | None,
    now: datetime,
) -> Alert | None:
    """Informational alert when portfolios hold opposing positions.

    Per CLAUDE.md: this is informational, not a warning. Each portfolio
    operates with its own thesis and risk management.
    """
    if a_side and b_side and a_side != b_side:
        return Alert(
            alert_type=AlertType.OPPOSING_POSITIONS,
            severity=AlertSeverity.INFO,
            route=None,
            message=f"Opposing positions: A is {a_side}, B is {b_side}",
            timestamp=now,
        )
    return None
