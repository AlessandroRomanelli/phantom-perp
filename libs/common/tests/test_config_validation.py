"""Tests for config schema validation and diff logging."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from libs.common.config import validate_strategy_config


@dataclass
class _SampleParams:
    """Simple params dataclass for testing."""

    param_a: int = 1
    param_b: float = 2.0


class TestValidateStrategyConfig:
    """Tests for validate_strategy_config()."""

    def test_unknown_top_level_key_raises(self) -> None:
        config: dict[str, Any] = {
            "strategy": {"name": "test", "enabled": True},
            "parameters": {"param_a": 5},
            "bogus_key": 1,
        }
        with pytest.raises(ValueError, match="unknown top-level keys"):
            validate_strategy_config("test", config, _SampleParams)

    def test_unknown_strategy_key_raises(self) -> None:
        config: dict[str, Any] = {
            "strategy": {"name": "test", "enabled": True, "bogus": 1},
        }
        with pytest.raises(ValueError, match="unknown strategy keys"):
            validate_strategy_config("test", config, _SampleParams)

    def test_unknown_parameter_key_raises(self) -> None:
        config: dict[str, Any] = {
            "parameters": {"nonexistent_param": 5},
        }
        with pytest.raises(ValueError, match="unknown parameter keys"):
            validate_strategy_config("test", config, _SampleParams)

    def test_valid_config_passes(self) -> None:
        config: dict[str, Any] = {
            "strategy": {"name": "test", "enabled": True, "weight": 0.3},
            "parameters": {"param_a": 10, "param_b": 3.0},
            "instruments": {
                "ETH-PERP": {"parameters": {"param_a": 15}},
            },
        }
        # Should not raise
        validate_strategy_config("test", config, _SampleParams)

    def test_unknown_instrument_param_warns_not_raises(self) -> None:
        config: dict[str, Any] = {
            "instruments": {
                "ETH-PERP": {"parameters": {"bad_key": 1}},
            },
        }
        with patch("libs.common.config._config_logger") as mock_logger:
            # Should NOT raise
            validate_strategy_config("test", config, _SampleParams)
            mock_logger.warning.assert_called_once_with(
                "unknown_instrument_params",
                strategy="test",
                instrument="ETH-PERP",
                unknown_keys=["bad_key"],
            )

    def test_instruments_key_is_valid_top_level(self) -> None:
        config: dict[str, Any] = {
            "strategy": {"name": "test", "enabled": True},
            "parameters": {"param_a": 5},
            "instruments": {},
        }
        # Should not raise
        validate_strategy_config("test", config, _SampleParams)
