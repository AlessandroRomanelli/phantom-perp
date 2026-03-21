"""Tests for confirmation config loading."""

from datetime import time, timedelta
from decimal import Decimal

from agents.confirmation.config import ConfirmationConfig, load_confirmation_config


class TestLoadConfirmationConfig:
    def test_empty_yaml_returns_defaults(self) -> None:
        cfg = load_confirmation_config({})
        assert cfg.default_ttl == timedelta(seconds=300)
        assert cfg.stale_price_threshold_pct == 1.0
        assert cfg.auto_approve.enabled is False
        assert cfg.quiet_hours.enabled is True
        assert cfg.batching.enabled is True

    def test_full_yaml_parsed(self) -> None:
        yaml_config = {
            "confirmation": {
                "default_ttl_seconds": 600,
                "stale_price_threshold_pct": 2.0,
                "auto_approve": {
                    "enabled": True,
                    "max_notional_usdc": 5000,
                    "min_conviction": 0.95,
                    "only_reduce": True,
                },
                "quiet_hours": {
                    "enabled": False,
                    "start": "22:00",
                    "end": "08:00",
                    "timezone": "US/Eastern",
                    "behavior": "reject",
                },
                "batching": {
                    "enabled": False,
                    "window_seconds": 10,
                    "max_batch_size": 3,
                },
            },
        }
        cfg = load_confirmation_config(yaml_config)
        assert cfg.default_ttl == timedelta(seconds=600)
        assert cfg.stale_price_threshold_pct == 2.0
        assert cfg.auto_approve.enabled is True
        assert cfg.auto_approve.max_notional_usdc == Decimal("5000")
        assert cfg.auto_approve.min_conviction == 0.95
        assert cfg.auto_approve.only_reduce is True
        assert cfg.quiet_hours.enabled is False
        assert cfg.quiet_hours.start == time(22, 0)
        assert cfg.quiet_hours.end == time(8, 0)
        assert cfg.quiet_hours.timezone == "US/Eastern"
        assert cfg.quiet_hours.behavior == "reject"
        assert cfg.batching.enabled is False
        assert cfg.batching.window == timedelta(seconds=10)
        assert cfg.batching.max_batch_size == 3

    def test_partial_yaml_fills_defaults(self) -> None:
        yaml_config = {
            "confirmation": {
                "default_ttl_seconds": 120,
            },
        }
        cfg = load_confirmation_config(yaml_config)
        assert cfg.default_ttl == timedelta(seconds=120)
        # Everything else should be defaults
        assert cfg.auto_approve.enabled is False
        assert cfg.quiet_hours.enabled is True
        assert cfg.batching.window == timedelta(seconds=30)

    def test_default_config_object(self) -> None:
        cfg = ConfirmationConfig()
        assert cfg.default_ttl == timedelta(seconds=300)
        assert cfg.auto_approve.max_notional_usdc == Decimal("2000")
        assert cfg.quiet_hours.timezone == "Europe/Zurich"
        assert cfg.batching.max_batch_size == 5
