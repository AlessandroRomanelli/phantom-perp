"""Tests for execution config loading."""

from datetime import timedelta

from agents.execution.config import ExecutionConfig, load_execution_config


class TestLoadExecutionConfig:
    def test_empty_yaml_returns_defaults(self) -> None:
        cfg = load_execution_config({})
        assert cfg.default_order_type == "limit"
        assert cfg.limit_offset_bps == 5
        assert cfg.order_ttl == timedelta(seconds=120)
        assert cfg.max_slippage_bps == 20
        assert cfg.retry_on_rejection is True
        assert cfg.max_retries == 2
        assert cfg.prefer_maker is True

    def test_full_yaml_parsed(self) -> None:
        yaml_config = {
            "execution": {
                "default_order_type": "market",
                "limit_offset_bps": 10,
                "order_ttl_seconds": 60,
                "max_slippage_bps": 30,
                "retry_on_rejection": False,
                "max_retries": 5,
                "prefer_maker": False,
            },
        }
        cfg = load_execution_config(yaml_config)
        assert cfg.default_order_type == "market"
        assert cfg.limit_offset_bps == 10
        assert cfg.order_ttl == timedelta(seconds=60)
        assert cfg.max_slippage_bps == 30
        assert cfg.retry_on_rejection is False
        assert cfg.max_retries == 5
        assert cfg.prefer_maker is False

    def test_partial_yaml_fills_defaults(self) -> None:
        yaml_config = {"execution": {"max_retries": 3}}
        cfg = load_execution_config(yaml_config)
        assert cfg.max_retries == 3
        assert cfg.prefer_maker is True  # default
        assert cfg.limit_offset_bps == 5  # default
