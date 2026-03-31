"""Tests for monitoring agent configuration."""

from agents.monitoring.config import (
    MonitoringConfig,
    PerformanceReportConfig,
    load_monitoring_config,
)


class TestLoadMonitoringConfig:
    def test_defaults(self) -> None:
        config = load_monitoring_config({})
        assert config.funding_alert_threshold_pct == 0.03
        assert config.margin_alert_threshold_pct == 50.0
        assert config.liquidation_distance_alert_pct == 15.0
        assert config.heartbeat_interval_seconds == 60
        assert config.ws_reconnect_max_delay_seconds == 30
        assert config.performance_report.frequency == "daily"

    def test_from_yaml(self) -> None:
        yaml = {
            "monitoring": {
                "funding_alert_threshold_pct": 0.05,
                "margin_alert_threshold_pct": 60.0,
                "liquidation_distance_alert_pct": 20.0,
                "ws_reconnect_max_delay_seconds": 45,
                "heartbeat_interval_seconds": 120,
                "performance_report": {
                    "frequency": "hourly",
                    "include_route_breakdown": False,
                    "include_funding_attribution": False,
                    "include_fee_breakdown": False,
                },
            },
        }
        config = load_monitoring_config(yaml)
        assert config.funding_alert_threshold_pct == 0.05
        assert config.margin_alert_threshold_pct == 60.0
        assert config.heartbeat_interval_seconds == 120
        assert config.performance_report.frequency == "hourly"
        assert config.performance_report.include_route_breakdown is False

    def test_partial_yaml(self) -> None:
        yaml = {"monitoring": {"heartbeat_interval_seconds": 90}}
        config = load_monitoring_config(yaml)
        assert config.heartbeat_interval_seconds == 90
        # Defaults for everything else
        assert config.funding_alert_threshold_pct == 0.03
        assert config.performance_report.frequency == "daily"
