"""Centralized configuration loading from environment variables and YAML files."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.common.instruments import load_instruments


class CoinbaseSettings(BaseSettings):
    """Coinbase INTX API credentials and endpoints.

    API keys on Coinbase INTX are portfolio-scoped: each key is created
    under a specific portfolio and can only operate on that portfolio.
    Therefore we need separate credentials for Portfolio A and Portfolio B.
    """

    model_config = SettingsConfigDict(env_prefix="COINBASE_INTX_")

    # Per-portfolio credentials (keys are portfolio-scoped on Coinbase INTX)
    api_key_a: str = ""
    api_secret_a: str = ""
    passphrase_a: str = ""
    api_key_b: str = ""
    api_secret_b: str = ""
    passphrase_b: str = ""

    # Shared endpoints (same for both portfolios)
    rest_url: str = "https://api.international.coinbase.com"
    ws_market_url: str = "wss://advanced-trade-ws.coinbase.com"
    ws_user_url: str = "wss://advanced-trade-ws-user.coinbase.com"


class TelegramSettings(BaseSettings):
    """Telegram bot configuration."""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = ""
    chat_id: str = ""
    webhook_url: str = ""


class InfraSettings(BaseSettings):
    """Infrastructure connection strings."""

    redis_url: str = "redis://localhost:6379"
    database_url: str = "postgresql://phantom:phantom_dev@localhost:5432/phantom_perp"
    log_level: str = "INFO"
    environment: str = "paper"


class PortfolioIDSettings(BaseSettings):
    """Portfolio ID configuration."""

    model_config = SettingsConfigDict(env_prefix="COINBASE_")

    portfolio_a_id: str = Field(default="75b5eaf1-53a6-483a-8ac0-d1da897dff2c")
    portfolio_b_id: str = Field(default="")


class AppSettings(BaseSettings):
    """Root settings aggregating all sub-settings."""

    coinbase: CoinbaseSettings = CoinbaseSettings()
    telegram: TelegramSettings = TelegramSettings()
    infra: InfraSettings = InfraSettings()
    portfolios: PortfolioIDSettings = PortfolioIDSettings()
    yaml_config: dict[str, Any] = Field(default_factory=dict)


_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


def load_yaml_config(profile: str = "default") -> dict[str, Any]:
    """Load a YAML configuration file by profile name.

    Args:
        profile: Config profile name (maps to configs/{profile}.yaml).

    Returns:
        Parsed YAML as a dictionary.
    """
    config_path = _CONFIG_DIR / f"{profile}.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_strategy_config(strategy_name: str) -> dict[str, Any]:
    """Load a strategy-specific YAML config.

    Args:
        strategy_name: Strategy name (maps to configs/strategies/{name}.yaml).

    Returns:
        Parsed YAML as a dictionary.
    """
    config_path = _CONFIG_DIR / "strategies" / f"{strategy_name}.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_strategy_config_for_instrument(
    strategy_name: str,
    instrument_id: str,
) -> dict[str, Any]:
    """Load strategy config with per-instrument parameter overrides.

    Reads the base strategy YAML, then merges any instrument-specific
    overrides from the ``instruments.<instrument_id>`` section.

    The merge is shallow on ``parameters``: instrument-level keys
    override base keys, unset keys keep the base value.

    An instrument section can also set ``enabled: false`` to disable
    the strategy for that specific instrument.

    Example YAML layout::

        strategy:
          name: regime_trend
          enabled: true
          weight: 0.30
        parameters:
          trend_ema_period: 50
          adx_threshold: 22
        instruments:
          BTC-PERP:
            parameters:
              trend_ema_period: 40
          SPY-PERP:
            enabled: false

    Args:
        strategy_name: Strategy name (maps to configs/strategies/{name}.yaml).
        instrument_id: Instrument to resolve overrides for.

    Returns:
        Merged config dict with ``strategy``, ``parameters``, and an
        ``_instrument`` key recording which instrument this is for.
    """
    base = load_strategy_config(strategy_name)
    if not base:
        return base

    result = dict(base)
    result["_instrument"] = instrument_id

    overrides = base.get("instruments", {}).get(instrument_id, {})
    if not overrides:
        return result

    # Merge strategy-level keys (enabled, weight)
    if "enabled" in overrides:
        result.setdefault("strategy", {})["enabled"] = overrides["enabled"]
    if "weight" in overrides:
        result.setdefault("strategy", {})["weight"] = overrides["weight"]

    # Merge parameters (shallow: override keys win, rest kept from base)
    if "parameters" in overrides:
        merged_params = dict(result.get("parameters", {}))
        merged_params.update(overrides["parameters"])
        result["parameters"] = merged_params

    return result


_config_logger = structlog.get_logger(__name__)

VALID_TOP_LEVEL_KEYS = {"strategy", "parameters", "instruments"}
VALID_STRATEGY_KEYS = {"name", "enabled", "weight"}


def validate_strategy_config(
    strategy_name: str,
    config: dict[str, Any],
    params_cls: type,
) -> None:
    """Validate strategy YAML config keys against known schema.

    Raises ValueError for unknown base-level keys (halts startup).
    Logs warning for unknown instrument-level keys (non-fatal).

    Args:
        strategy_name: Name of the strategy (for error messages).
        config: Parsed YAML config dict.
        params_cls: The strategy's Params dataclass class.

    Raises:
        ValueError: If unknown keys found at base level.
    """
    # Skip internal keys added by merge logic
    internal_keys = {"_instrument"}

    # Check top-level keys (D-06: error and halt)
    unknown_top = set(config.keys()) - VALID_TOP_LEVEL_KEYS - internal_keys
    if unknown_top:
        raise ValueError(
            f"Strategy '{strategy_name}': unknown top-level keys: {sorted(unknown_top)}"
        )

    # Check strategy block keys (D-06)
    strategy_block = config.get("strategy", {})
    if strategy_block:
        unknown_strategy = set(strategy_block.keys()) - VALID_STRATEGY_KEYS
        if unknown_strategy:
            raise ValueError(
                f"Strategy '{strategy_name}': unknown strategy keys: {sorted(unknown_strategy)}"
            )

    # Check parameter keys against dataclass fields (D-05: error and halt)
    valid_params = {f.name for f in dataclasses.fields(params_cls)}
    yaml_params = set(config.get("parameters", {}).keys())
    unknown_params = yaml_params - valid_params
    if unknown_params:
        raise ValueError(
            f"Strategy '{strategy_name}': unknown parameter keys: {sorted(unknown_params)}"
        )

    # Check instrument-level parameter keys (D-07: warn, don't halt)
    for instrument_id, overrides in config.get("instruments", {}).items():
        if isinstance(overrides, dict) and "parameters" in overrides:
            inst_params = set(overrides["parameters"].keys())
            unknown_inst = inst_params - valid_params
            if unknown_inst:
                _config_logger.warning(
                    "unknown_instrument_params",
                    strategy=strategy_name,
                    instrument=instrument_id,
                    unknown_keys=sorted(unknown_inst),
                )


def log_config_diff(
    strategy_name: str,
    instrument_id: str,
    merged_params: dict[str, Any],
    default_params: dict[str, Any],
) -> None:
    """Log which parameters differ from defaults for this instrument.

    Args:
        strategy_name: Strategy name for log context.
        instrument_id: Instrument ID for log context.
        merged_params: Final merged parameter dict.
        default_params: Base default parameter dict.
    """
    diffs: dict[str, dict[str, Any]] = {}
    for key, merged_val in merged_params.items():
        default_val = default_params.get(key)
        if merged_val != default_val:
            diffs[key] = {"default": default_val, "override": merged_val}

    if diffs:
        _config_logger.info(
            "instrument_config_overrides",
            strategy=strategy_name,
            instrument=instrument_id,
            overrides=diffs,
        )
    else:
        _config_logger.info(
            "instrument_using_defaults",
            strategy=strategy_name,
            instrument=instrument_id,
        )


def get_settings() -> AppSettings:
    """Build and return the full application settings.

    Reads from environment variables (via pydantic-settings) and merges
    with the YAML config determined by the ENVIRONMENT env var.
    """
    infra = InfraSettings()
    yaml_config = load_yaml_config(infra.environment)
    # Fall back to default if environment-specific config is empty
    if not yaml_config:
        yaml_config = load_yaml_config("default")

    # Instruments are always defined in default.yaml (canonical source).
    # Environment-specific configs (paper, live) override other settings
    # but do not duplicate the instruments list.
    default_config = load_yaml_config("default")
    load_instruments(default_config)

    return AppSettings(
        coinbase=CoinbaseSettings(),
        telegram=TelegramSettings(),
        infra=infra,
        portfolios=PortfolioIDSettings(),
        yaml_config=yaml_config,
    )
