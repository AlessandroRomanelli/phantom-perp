"""Tests for config schema validation, diff logging, and CoinbaseSettings env binding."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from libs.common.config import CoinbaseSettings, PortfolioIDSettings, log_config_diff, validate_strategy_config


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


class TestLogConfigDiff:
    """Tests for log_config_diff()."""

    def test_log_config_diff_shows_overrides(self) -> None:
        with patch("libs.common.config._config_logger") as mock_logger:
            log_config_diff(
                "mean_reversion",
                "ETH-PERP",
                {"bb_period": 15, "bb_std": 2.0},
                {"bb_period": 20, "bb_std": 2.0},
            )
            mock_logger.info.assert_called_once_with(
                "instrument_config_overrides",
                strategy="mean_reversion",
                instrument="ETH-PERP",
                overrides={"bb_period": {"default": 20, "override": 15}},
            )

    def test_log_config_diff_shows_defaults_when_no_overrides(self) -> None:
        with patch("libs.common.config._config_logger") as mock_logger:
            log_config_diff(
                "momentum",
                "BTC-PERP",
                {"fast_ema": 12, "slow_ema": 26},
                {"fast_ema": 12, "slow_ema": 26},
            )
            mock_logger.info.assert_called_once_with(
                "instrument_using_defaults",
                strategy="momentum",
                instrument="BTC-PERP",
            )


class TestCoinbaseSettingsEnvPrefix:
    """Tests for CoinbaseSettings env var binding (MIG-05)."""

    def test_coinbase_settings_reads_adv_env_prefix(self) -> None:
        """CoinbaseSettings binds to COINBASE_ADV_ prefixed env vars."""
        env = {
            "COINBASE_ADV_API_KEY_A": "test-key-a-uuid",
            "COINBASE_ADV_API_SECRET_A": "test-secret-a-pem",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = CoinbaseSettings()
            assert settings.api_key_a == "test-key-a-uuid"
            assert settings.api_secret_a == "test-secret-a-pem"

    def test_old_intx_prefix_not_read(self) -> None:
        """Old COINBASE_INTX_ prefixed vars are NOT picked up."""
        env = {
            "COINBASE_INTX_API_KEY_A": "old-intx-key",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = CoinbaseSettings()
            assert settings.api_key_a != "old-intx-key"

    def test_passphrase_field_does_not_exist(self) -> None:
        """passphrase_a and passphrase_b attributes removed per D-09."""
        settings = CoinbaseSettings()
        assert not hasattr(settings, "passphrase_a"), \
            "passphrase_a should not exist on CoinbaseSettings"
        assert not hasattr(settings, "passphrase_b"), \
            "passphrase_b should not exist on CoinbaseSettings"

    def test_env_prefix_is_coinbase_adv(self) -> None:
        """model_config env_prefix is exactly COINBASE_ADV_."""
        assert CoinbaseSettings.model_config.get("env_prefix") == "COINBASE_ADV_"

    def test_rest_url_default(self) -> None:
        """Default rest_url points to api.coinbase.com."""
        settings = CoinbaseSettings()
        assert settings.rest_url == "https://api.coinbase.com"

    def test_b_credential_fields_removed(self) -> None:
        """api_key_b and api_secret_b no longer exist on CoinbaseSettings (D-single-client)."""
        settings = CoinbaseSettings()
        assert not hasattr(settings, "api_key_b"), \
            "api_key_b should not exist after single-client collapse"
        assert not hasattr(settings, "api_secret_b"), \
            "api_secret_b should not exist after single-client collapse"


class TestPortfolioIDSettingsSingleField:
    """PortfolioIDSettings exposes a single portfolio_id field (D-single-client)."""

    def test_portfolio_id_field_exists(self) -> None:
        """PortfolioIDSettings has portfolio_id attribute."""
        settings = PortfolioIDSettings()
        assert hasattr(settings, "portfolio_id")

    def test_portfolio_a_id_and_portfolio_b_id_removed(self) -> None:
        """portfolio_a_id and portfolio_b_id do not exist after single-client collapse."""
        settings = PortfolioIDSettings()
        assert not hasattr(settings, "portfolio_a_id"), \
            "portfolio_a_id should not exist"
        assert not hasattr(settings, "portfolio_b_id"), \
            "portfolio_b_id should not exist"

    def test_portfolio_id_env_binding(self) -> None:
        """COINBASE_PORTFOLIO_ID env var is read into portfolio_id."""
        with patch.dict(os.environ, {"COINBASE_PORTFOLIO_ID": "test-uuid"}, clear=False):
            settings = PortfolioIDSettings()
            assert settings.portfolio_id == "test-uuid"
