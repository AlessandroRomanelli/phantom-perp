"""Centralized configuration loading from environment variables and YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    return AppSettings(
        coinbase=CoinbaseSettings(),
        telegram=TelegramSettings(),
        infra=infra,
        portfolios=PortfolioIDSettings(),
        yaml_config=yaml_config,
    )
