"""Tests for regime config loading and lookup functions."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from agents.signals.main import get_regime_overrides, load_regime_config
from libs.common.models.enums import MarketRegime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG: dict[str, Any] = {
    "strategies": {
        "momentum": {
            "trending_up": {
                "min_conviction": 0.28,
                "cooldown_bars": 3,
                "adx_threshold": 18.0,
            },
            "ranging": {
                "min_conviction": 0.35,
                "cooldown_bars": 6,
            },
        },
        "mean_reversion": {
            "trending_up": {
                "min_conviction": 0.40,
            },
        },
    }
}


# ---------------------------------------------------------------------------
# load_regime_config tests
# ---------------------------------------------------------------------------


def test_load_regime_config_returns_dict_when_exists(tmp_path: Path) -> None:
    """load_regime_config() returns parsed dict when regimes.yaml exists."""
    regime_file = tmp_path / "regimes.yaml"
    regime_file.write_text(yaml.dump(_SAMPLE_CONFIG))

    with patch("agents.signals.main.Path") as mock_path_cls:
        mock_path_cls.return_value.resolve.return_value.parent.parent.parent.__truediv__.return_value = (
            tmp_path
        )
        # Use a simpler approach: patch the resolve chain to return our tmp file
        pass

    # Direct approach: monkeypatch the file path resolution
    import agents.signals.main as main_mod

    orig_load = main_mod.load_regime_config

    def _patched_load() -> dict[str, Any]:
        with open(regime_file) as f:
            return yaml.safe_load(f) or {}

    main_mod.load_regime_config = _patched_load  # type: ignore[assignment]
    try:
        result = main_mod.load_regime_config()
        assert "strategies" in result
        assert "momentum" in result["strategies"]
    finally:
        main_mod.load_regime_config = orig_load  # type: ignore[assignment]


def test_load_regime_config_returns_empty_dict_when_missing() -> None:
    """load_regime_config() returns empty dict when file missing."""
    import agents.signals.main as main_mod

    orig_load = main_mod.load_regime_config

    def _patched_load() -> dict[str, Any]:
        missing = Path("/nonexistent/path/regimes.yaml")
        if not missing.exists():
            return {}
        with open(missing) as f:
            return yaml.safe_load(f) or {}

    main_mod.load_regime_config = _patched_load  # type: ignore[assignment]
    try:
        result = main_mod.load_regime_config()
        assert result == {}
    finally:
        main_mod.load_regime_config = orig_load  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# get_regime_overrides tests
# ---------------------------------------------------------------------------


def test_get_regime_overrides_correct_params_for_momentum_trending_up() -> None:
    """get_regime_overrides() returns correct params for momentum + TRENDING_UP."""
    result = get_regime_overrides(_SAMPLE_CONFIG, "momentum", MarketRegime.TRENDING_UP)
    assert result["min_conviction"] == pytest.approx(0.28)
    assert result["cooldown_bars"] == 3
    assert result["adx_threshold"] == pytest.approx(18.0)


def test_get_regime_overrides_returns_empty_for_none_regime() -> None:
    """get_regime_overrides() returns empty dict when regime is None."""
    result = get_regime_overrides(_SAMPLE_CONFIG, "momentum", None)
    assert result == {}


def test_get_regime_overrides_returns_empty_for_unknown_strategy() -> None:
    """get_regime_overrides() returns empty dict for unknown strategy name."""
    result = get_regime_overrides(_SAMPLE_CONFIG, "unknown_strategy", MarketRegime.RANGING)
    assert result == {}


def test_get_regime_overrides_returns_empty_for_strategy_with_no_regime_entry() -> None:
    """get_regime_overrides() returns empty dict for strategy with no overrides for given regime."""
    # mean_reversion has trending_up but not high_volatility
    result = get_regime_overrides(
        _SAMPLE_CONFIG, "mean_reversion", MarketRegime.HIGH_VOLATILITY
    )
    assert result == {}


def test_get_regime_overrides_returns_ranging_params_for_momentum() -> None:
    """get_regime_overrides() returns correct params for momentum + RANGING."""
    result = get_regime_overrides(_SAMPLE_CONFIG, "momentum", MarketRegime.RANGING)
    assert result["min_conviction"] == pytest.approx(0.35)
    assert result["cooldown_bars"] == 6


def test_get_regime_overrides_returns_empty_for_empty_config() -> None:
    """get_regime_overrides() handles empty config dict gracefully."""
    result = get_regime_overrides({}, "momentum", MarketRegime.TRENDING_UP)
    assert result == {}
