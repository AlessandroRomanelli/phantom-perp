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

    def test_instrument_limit_offset_bps_parsed(self) -> None:
        yaml_config = {
            "execution": {
                "limit_offset_bps": 5,
                "instruments": {
                    "BTC-PERP": {"limit_offset_bps": 2},
                    "ETH-PERP": {"limit_offset_bps": 3},
                    "SOL-PERP": {"limit_offset_bps": 5},
                },
            },
        }
        cfg = load_execution_config(yaml_config)
        assert cfg.instrument_limit_offset_bps["BTC-PERP"] == 2
        assert cfg.instrument_limit_offset_bps["SOL-PERP"] == 5

    def test_instrument_limit_offset_bps_fallback(self) -> None:
        """Instrument not in map returns the global default."""
        yaml_config = {
            "execution": {
                "limit_offset_bps": 5,
                "instruments": {
                    "BTC-PERP": {"limit_offset_bps": 2},
                },
            },
        }
        cfg = load_execution_config(yaml_config)
        assert cfg.resolve_limit_offset_bps("XYZ-PERP") == 5

    def test_instrument_limit_offset_bps_empty_instruments(self) -> None:
        """No instruments key → empty dict; resolve returns global default."""
        yaml_config = {"execution": {"limit_offset_bps": 7}}
        cfg = load_execution_config(yaml_config)
        assert cfg.instrument_limit_offset_bps == {}
        assert cfg.resolve_limit_offset_bps("BTC-PERP") == 7

    def test_sl_limit_buffer_bps_default(self) -> None:
        """sl_limit_buffer_bps defaults to 10 when not in YAML."""
        cfg = load_execution_config({})
        assert cfg.sl_limit_buffer_bps == 10

    def test_sl_limit_buffer_bps_parsed(self) -> None:
        """sl_limit_buffer_bps is parsed from execution section."""
        cfg = load_execution_config({"execution": {"sl_limit_buffer_bps": 15}})
        assert cfg.sl_limit_buffer_bps == 15
