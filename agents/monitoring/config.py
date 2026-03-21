"""Monitoring agent configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PerformanceReportConfig:
    """Settings for periodic performance reports."""

    frequency: str = "daily"
    include_portfolio_breakdown: bool = True
    include_funding_attribution: bool = True
    include_fee_breakdown: bool = True


@dataclass(frozen=True, slots=True)
class MonitoringConfig:
    """All monitoring-agent settings."""

    funding_alert_threshold_pct: float = 0.03
    margin_alert_threshold_pct: float = 50.0
    liquidation_distance_alert_pct: float = 15.0
    ws_reconnect_max_delay_seconds: int = 30
    heartbeat_interval_seconds: int = 60
    performance_report: PerformanceReportConfig = PerformanceReportConfig()


def load_monitoring_config(yaml_config: dict[str, Any]) -> MonitoringConfig:
    """Build a MonitoringConfig from the parsed default.yaml dict."""
    section = yaml_config.get("monitoring", {})
    if not section:
        return MonitoringConfig()

    report_section = section.get("performance_report", {})
    report_config = PerformanceReportConfig(
        frequency=report_section.get("frequency", "daily"),
        include_portfolio_breakdown=report_section.get(
            "include_portfolio_breakdown", True,
        ),
        include_funding_attribution=report_section.get(
            "include_funding_attribution", True,
        ),
        include_fee_breakdown=report_section.get("include_fee_breakdown", True),
    )

    return MonitoringConfig(
        funding_alert_threshold_pct=float(
            section.get("funding_alert_threshold_pct", 0.03),
        ),
        margin_alert_threshold_pct=float(
            section.get("margin_alert_threshold_pct", 50.0),
        ),
        liquidation_distance_alert_pct=float(
            section.get("liquidation_distance_alert_pct", 15.0),
        ),
        ws_reconnect_max_delay_seconds=int(
            section.get("ws_reconnect_max_delay_seconds", 30),
        ),
        heartbeat_interval_seconds=int(
            section.get("heartbeat_interval_seconds", 60),
        ),
        performance_report=report_config,
    )
