"""Calibration validation tests for regime_leverage defaults in configs/default.yaml.

Validates that the shipped default values satisfy:
  1. Hierarchy: trending > ranging > high_vol (both routes)
  2. low_vol > ranging (both routes)
  3. All values are within bounds.yaml min/max limits
  4. All values are below the hard caps (10x Route A, 5x Route B)
  5. Integration: get_regime_leverage_cap() returns expected values from real config
"""

from decimal import Decimal

import pytest
import yaml

from agents.risk.dynamic_leverage import get_regime_leverage_cap
from libs.common.config import load_yaml_config
from libs.common.constants import MAX_LEVERAGE_GLOBAL, MAX_LEVERAGE_ROUTE_B
from libs.common.models.enums import MarketRegime, Route

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def default_config() -> dict:
    """Load configs/default.yaml once for all tests in the module."""
    cfg = load_yaml_config("default")
    assert cfg, "configs/default.yaml must not be empty"
    return cfg


@pytest.fixture(scope="module")
def regime_leverage_config(default_config: dict) -> dict:
    """Return the risk.regime_leverage block from default config."""
    return default_config["risk"]["regime_leverage"]


@pytest.fixture(scope="module")
def bounds() -> dict:
    """Load configs/bounds.yaml once for all tests in the module."""
    import importlib.resources
    from pathlib import Path

    bounds_path = Path(__file__).resolve().parent.parent.parent / "configs" / "bounds.yaml"
    with open(bounds_path) as f:
        data = yaml.safe_load(f)
    assert data, "configs/bounds.yaml must not be empty"
    return data


# ---------------------------------------------------------------------------
# Test 1 & 2: Hierarchy — Route A: trending > ranging > high_vol
# ---------------------------------------------------------------------------


class TestLeverageHierarchyRouteA:
    """Trending regime should allow more leverage than ranging or high-vol (Route A)."""

    def test_trending_up_exceeds_ranging_route_a(self, regime_leverage_config: dict) -> None:
        a = regime_leverage_config["route_a"]
        assert a["trending_up"] > a["ranging"], (
            f"Route A: trending_up ({a['trending_up']}) must exceed ranging ({a['ranging']})"
        )

    def test_trending_down_exceeds_ranging_route_a(self, regime_leverage_config: dict) -> None:
        a = regime_leverage_config["route_a"]
        assert a["trending_down"] > a["ranging"], (
            f"Route A: trending_down ({a['trending_down']}) must exceed ranging ({a['ranging']})"
        )

    def test_ranging_exceeds_high_vol_route_a(self, regime_leverage_config: dict) -> None:
        a = regime_leverage_config["route_a"]
        assert a["ranging"] > a["high_volatility"], (
            f"Route A: ranging ({a['ranging']}) must exceed high_volatility ({a['high_volatility']})"
        )

    def test_low_vol_exceeds_ranging_route_a(self, regime_leverage_config: dict) -> None:
        """Low-vol regime should be more permissive than ranging (calm market = higher leverage)."""
        a = regime_leverage_config["route_a"]
        assert a["low_volatility"] > a["ranging"], (
            f"Route A: low_volatility ({a['low_volatility']}) must exceed ranging ({a['ranging']})"
        )


# ---------------------------------------------------------------------------
# Test 3 & 4: Hierarchy — Route B: same relationships, stricter absolute values
# ---------------------------------------------------------------------------


class TestLeverageHierarchyRouteB:
    """Same directional hierarchy must hold for Route B."""

    def test_trending_up_exceeds_ranging_route_b(self, regime_leverage_config: dict) -> None:
        b = regime_leverage_config["route_b"]
        assert b["trending_up"] > b["ranging"], (
            f"Route B: trending_up ({b['trending_up']}) must exceed ranging ({b['ranging']})"
        )

    def test_ranging_exceeds_high_vol_route_b(self, regime_leverage_config: dict) -> None:
        b = regime_leverage_config["route_b"]
        assert b["ranging"] > b["high_volatility"], (
            f"Route B: ranging ({b['ranging']}) must exceed high_volatility ({b['high_volatility']})"
        )

    def test_low_vol_exceeds_ranging_route_b(self, regime_leverage_config: dict) -> None:
        """Low-vol regime should be more permissive than ranging (Route B)."""
        b = regime_leverage_config["route_b"]
        assert b["low_volatility"] > b["ranging"], (
            f"Route B: low_volatility ({b['low_volatility']}) must exceed ranging ({b['ranging']})"
        )


# ---------------------------------------------------------------------------
# Test 5: All values within bounds.yaml limits
# ---------------------------------------------------------------------------


class TestValuesWithinBoundsYaml:
    """Each default value must fall within the min/max declared in bounds.yaml."""

    def test_trending_values_within_bounds(
        self, regime_leverage_config: dict, bounds: dict
    ) -> None:
        b = bounds["regime_leverage_trending"]
        for route_key in ("route_a", "route_b"):
            for regime in ("trending_up", "trending_down"):
                val = regime_leverage_config[route_key][regime]
                assert b["min"] <= val <= b["max"], (
                    f"{route_key}.{regime}={val} outside bounds [{b['min']}, {b['max']}]"
                )

    def test_ranging_values_within_bounds(
        self, regime_leverage_config: dict, bounds: dict
    ) -> None:
        b = bounds["regime_leverage_ranging"]
        for route_key in ("route_a", "route_b"):
            val = regime_leverage_config[route_key]["ranging"]
            assert b["min"] <= val <= b["max"], (
                f"{route_key}.ranging={val} outside bounds [{b['min']}, {b['max']}]"
            )

    def test_high_vol_values_within_bounds(
        self, regime_leverage_config: dict, bounds: dict
    ) -> None:
        b = bounds["regime_leverage_high_vol"]
        for route_key in ("route_a", "route_b"):
            val = regime_leverage_config[route_key]["high_volatility"]
            assert b["min"] <= val <= b["max"], (
                f"{route_key}.high_volatility={val} outside bounds [{b['min']}, {b['max']}]"
            )


# ---------------------------------------------------------------------------
# Test 6: All values below hard caps
# ---------------------------------------------------------------------------


class TestValuesWithinHardCaps:
    """Every configured value must not exceed the hard safety cap for each route."""

    def test_all_route_a_values_below_hard_cap(self, regime_leverage_config: dict) -> None:
        cap = float(MAX_LEVERAGE_GLOBAL)
        a = regime_leverage_config["route_a"]
        for regime, val in a.items():
            assert val <= cap, f"Route A {regime}={val} exceeds hard cap {cap}"

    def test_all_route_b_values_below_hard_cap(self, regime_leverage_config: dict) -> None:
        cap = float(MAX_LEVERAGE_ROUTE_B)
        b = regime_leverage_config["route_b"]
        for regime, val in b.items():
            assert val <= cap, f"Route B {regime}={val} exceeds hard cap {cap}"


# ---------------------------------------------------------------------------
# Test 7 & 8: Integration — get_regime_leverage_cap() with real loaded config
# ---------------------------------------------------------------------------


class TestGetRegimeLeverageCapIntegration:
    """Integration tests: get_regime_leverage_cap() uses real config values."""

    def test_route_a_trending_up_returns_config_value(self, default_config: dict) -> None:
        expected = Decimal(
            str(default_config["risk"]["regime_leverage"]["route_a"]["trending_up"])
        )
        result = get_regime_leverage_cap(MarketRegime.TRENDING_UP, Route.A, default_config)
        assert result == expected, f"Expected {expected}, got {result}"

    def test_route_b_high_volatility_returns_config_value(self, default_config: dict) -> None:
        expected = Decimal(
            str(default_config["risk"]["regime_leverage"]["route_b"]["high_volatility"])
        )
        result = get_regime_leverage_cap(MarketRegime.HIGH_VOLATILITY, Route.B, default_config)
        assert result == expected, f"Expected {expected}, got {result}"
