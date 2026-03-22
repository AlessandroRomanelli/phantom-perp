"""Tests for session config loading, override application, and conviction normalization."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agents.signals.main import (
    _apply_conviction_normalization,
    _apply_session_overrides,
    _restore_params,
    get_session_overrides,
    load_session_config,
)
from agents.signals.session_classifier import SessionType
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal


def test_load_session_config_returns_strategies() -> None:
    """load_session_config() returns a dict with a 'strategies' key."""
    config = load_session_config()
    assert isinstance(config, dict)
    assert "strategies" in config
    assert "momentum" in config["strategies"]
    assert "mean_reversion" in config["strategies"]
    assert "vwap" in config["strategies"]


def test_load_session_config_has_instrument_types() -> None:
    """Session config includes instrument_types with crypto and equity."""
    config = load_session_config()
    assert "instrument_types" in config
    assert "crypto" in config["instrument_types"]
    assert "equity" in config["instrument_types"]
    assert "ETH-PERP" in config["instrument_types"]["crypto"]
    assert "QQQ-PERP" in config["instrument_types"]["equity"]


def test_get_session_overrides_momentum_crypto_weekend() -> None:
    """get_session_overrides returns correct overrides for momentum + crypto_weekend."""
    config = load_session_config()
    overrides = get_session_overrides(
        config, "momentum", SessionType.CRYPTO_WEEKEND, "ETH-PERP",
    )
    assert overrides["min_conviction"] == 0.30
    assert overrides["cooldown_bars"] == 3
    assert overrides["vol_min_ratio"] == 0.3


def test_get_session_overrides_unknown_strategy() -> None:
    """get_session_overrides returns empty dict for unknown strategy."""
    config = load_session_config()
    overrides = get_session_overrides(
        config, "unknown_strategy", SessionType.CRYPTO_WEEKEND, "ETH-PERP",
    )
    assert overrides == {}


def test_get_session_overrides_equity_off_hours_for_qqq() -> None:
    """QQQ-PERP during crypto_weekday session uses equity_off_hours overrides."""
    config = load_session_config()
    overrides = get_session_overrides(
        config, "momentum", SessionType.CRYPTO_WEEKDAY, "QQQ-PERP",
    )
    # Should get equity_off_hours overrides for equity instrument
    assert overrides["min_conviction"] == 0.35
    assert overrides["cooldown_bars"] == 4


def test_get_session_overrides_crypto_weekday_for_eth() -> None:
    """ETH-PERP during crypto_weekday gets crypto_weekday overrides (empty if not defined)."""
    config = load_session_config()
    overrides = get_session_overrides(
        config, "momentum", SessionType.CRYPTO_WEEKDAY, "ETH-PERP",
    )
    # crypto_weekday is not defined for momentum, so empty
    assert overrides == {}


def test_conviction_normalization_adds_band_high() -> None:
    """Conviction normalization adds conviction_band metadata for high conviction."""
    signal = StandardSignal(
        signal_id="test-1",
        timestamp=datetime.now(tz=timezone.utc),
        instrument="ETH-PERP",
        direction=PositionSide.LONG,
        conviction=0.80,
        source=SignalSource.MOMENTUM,
        time_horizon=timedelta(hours=4),
        reasoning="test",
        suggested_target=PortfolioTarget.B,
        entry_price=Decimal("2000"),
        metadata={},
    )
    result = _apply_conviction_normalization(signal)
    assert result.metadata["conviction_band"] == "high"
    assert result.suggested_target == PortfolioTarget.A  # Unified threshold 0.70


def test_conviction_normalization_low_keeps_target() -> None:
    """Low conviction signal keeps its original suggested_target."""
    signal = StandardSignal(
        signal_id="test-2",
        timestamp=datetime.now(tz=timezone.utc),
        instrument="ETH-PERP",
        direction=PositionSide.SHORT,
        conviction=0.45,
        source=SignalSource.MEAN_REVERSION,
        time_horizon=timedelta(hours=8),
        reasoning="test",
        suggested_target=PortfolioTarget.B,
        entry_price=Decimal("2000"),
        metadata={"existing_key": "value"},
    )
    result = _apply_conviction_normalization(signal)
    assert result.metadata["conviction_band"] == "low"
    assert result.suggested_target == PortfolioTarget.B
    assert result.metadata["existing_key"] == "value"


def test_conviction_normalization_medium_band() -> None:
    """Medium conviction (0.50-0.69) gets medium band but no Portfolio A routing."""
    signal = StandardSignal(
        signal_id="test-3",
        timestamp=datetime.now(tz=timezone.utc),
        instrument="BTC-PERP",
        direction=PositionSide.LONG,
        conviction=0.60,
        source=SignalSource.CORRELATION,
        time_horizon=timedelta(hours=6),
        reasoning="test",
        metadata={},
    )
    result = _apply_conviction_normalization(signal)
    assert result.metadata["conviction_band"] == "medium"
    assert result.suggested_target is None or result.suggested_target == PortfolioTarget.B


def test_apply_and_restore_session_overrides() -> None:
    """Session overrides are applied and then correctly restored."""
    from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy

    strategy = MomentumStrategy(params=MomentumParams(min_conviction=0.50, cooldown_bars=5))

    assert strategy._params.min_conviction == 0.50
    assert strategy._params.cooldown_bars == 5

    overrides = {"min_conviction": 0.30, "cooldown_bars": 3}
    originals = _apply_session_overrides(strategy, overrides)

    assert strategy._params.min_conviction == 0.30
    assert strategy._params.cooldown_bars == 3
    assert originals == {"min_conviction": 0.50, "cooldown_bars": 5}

    _restore_params(strategy, originals)

    assert strategy._params.min_conviction == 0.50
    assert strategy._params.cooldown_bars == 5
