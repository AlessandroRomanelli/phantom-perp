"""Unit tests for agents/risk/dynamic_leverage.py pure functions."""

from decimal import Decimal

import pytest

from agents.risk.dynamic_leverage import (
    compute_effective_leverage_cap,
    compute_stop_distance_leverage,
    get_regime_leverage_cap,
)
from libs.common.constants import MAX_LEVERAGE_GLOBAL, MAX_LEVERAGE_ROUTE_B
from libs.common.models.enums import MarketRegime, Route

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REGIME_LEVERAGE_CONFIG: dict = {
    "risk": {
        "regime_leverage": {
            "route_a": {
                "trending_up": 8.0,
                "trending_down": 8.0,
                "ranging": 4.0,
                "high_volatility": 2.0,
                "low_volatility": 5.0,
                "squeeze": 3.0,
            },
            "route_b": {
                "trending_up": 4.0,
                "trending_down": 4.0,
                "ranging": 2.0,
                "high_volatility": 1.5,
                "low_volatility": 3.0,
                "squeeze": 2.0,
            },
        }
    }
}

EMPTY_CONFIG: dict = {}


# ---------------------------------------------------------------------------
# get_regime_leverage_cap
# ---------------------------------------------------------------------------


class TestGetRegimeLeverageCap:
    """Tests for get_regime_leverage_cap."""

    @pytest.mark.parametrize(
        "regime,expected",
        [
            (MarketRegime.TRENDING_UP, Decimal("8.0")),
            (MarketRegime.TRENDING_DOWN, Decimal("8.0")),
            (MarketRegime.RANGING, Decimal("4.0")),
            (MarketRegime.HIGH_VOLATILITY, Decimal("2.0")),
            (MarketRegime.LOW_VOLATILITY, Decimal("5.0")),
            (MarketRegime.SQUEEZE, Decimal("3.0")),
        ],
    )
    def test_route_a_all_regimes(self, regime: MarketRegime, expected: Decimal) -> None:
        cap = get_regime_leverage_cap(regime, Route.A, REGIME_LEVERAGE_CONFIG)
        assert cap == expected

    @pytest.mark.parametrize(
        "regime,expected",
        [
            (MarketRegime.TRENDING_UP, Decimal("4.0")),
            (MarketRegime.TRENDING_DOWN, Decimal("4.0")),
            (MarketRegime.RANGING, Decimal("2.0")),
            (MarketRegime.HIGH_VOLATILITY, Decimal("1.5")),
            (MarketRegime.LOW_VOLATILITY, Decimal("3.0")),
            (MarketRegime.SQUEEZE, Decimal("2.0")),
        ],
    )
    def test_route_b_all_regimes(self, regime: MarketRegime, expected: Decimal) -> None:
        cap = get_regime_leverage_cap(regime, Route.B, REGIME_LEVERAGE_CONFIG)
        assert cap == expected

    def test_missing_config_route_a_fallback(self) -> None:
        """Empty config falls back to default Route A cap of 3.0."""
        cap = get_regime_leverage_cap(MarketRegime.TRENDING_UP, Route.A, EMPTY_CONFIG)
        assert cap == Decimal("3.0")

    def test_missing_config_route_b_fallback(self) -> None:
        """Empty config falls back to default Route B cap of 2.0."""
        cap = get_regime_leverage_cap(MarketRegime.RANGING, Route.B, EMPTY_CONFIG)
        assert cap == Decimal("2.0")

    def test_missing_regime_key_falls_back_to_default(self) -> None:
        """Config with a missing regime key uses the default cap."""
        partial_config: dict = {
            "risk": {"regime_leverage": {"route_a": {}, "route_b": {}}}
        }
        cap = get_regime_leverage_cap(MarketRegime.TRENDING_UP, Route.A, partial_config)
        assert cap == Decimal("3.0")

    def test_config_above_hard_cap_route_a_is_clamped(self) -> None:
        """Config value above MAX_LEVERAGE_GLOBAL is clamped to hard cap."""
        oversize_config: dict = {
            "risk": {
                "regime_leverage": {
                    "route_a": {"trending_up": 99.0},
                    "route_b": {},
                }
            }
        }
        cap = get_regime_leverage_cap(
            MarketRegime.TRENDING_UP, Route.A, oversize_config
        )
        assert cap == MAX_LEVERAGE_GLOBAL

    def test_config_above_hard_cap_route_b_is_clamped(self) -> None:
        """Config value above MAX_LEVERAGE_ROUTE_B is clamped to Route B hard cap."""
        oversize_config: dict = {
            "risk": {
                "regime_leverage": {
                    "route_a": {},
                    "route_b": {"ranging": 99.0},
                }
            }
        }
        cap = get_regime_leverage_cap(MarketRegime.RANGING, Route.B, oversize_config)
        assert cap == MAX_LEVERAGE_ROUTE_B

    def test_result_never_exceeds_hard_cap_route_a(self) -> None:
        """Across all regimes the cap must never exceed MAX_LEVERAGE_GLOBAL."""
        for regime in MarketRegime:
            cap = get_regime_leverage_cap(regime, Route.A, REGIME_LEVERAGE_CONFIG)
            assert cap <= MAX_LEVERAGE_GLOBAL, f"Route A cap exceeded for {regime}"

    def test_result_never_exceeds_hard_cap_route_b(self) -> None:
        """Across all regimes the cap must never exceed MAX_LEVERAGE_ROUTE_B."""
        for regime in MarketRegime:
            cap = get_regime_leverage_cap(regime, Route.B, REGIME_LEVERAGE_CONFIG)
            assert cap <= MAX_LEVERAGE_ROUTE_B, f"Route B cap exceeded for {regime}"


# ---------------------------------------------------------------------------
# compute_stop_distance_leverage
# ---------------------------------------------------------------------------


class TestComputeStopDistanceLeverage:
    """Tests for compute_stop_distance_leverage."""

    def test_wide_stop_yields_low_leverage(self) -> None:
        """10% stop distance with 2% risk budget → 0.2x, clamped up to floor=1."""
        # stop 10% away: 2000 entry, 1800 stop → 200/2000 = 0.10 fraction
        # leverage = 0.02 / 0.10 = 0.2 → clamped to 1.0
        entry = Decimal("2000")
        stop = Decimal("1800")
        regime_cap = Decimal("8.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result == Decimal("1.0")

    def test_tight_stop_yields_high_leverage_clamped_to_cap(self) -> None:
        """0.25% stop distance → 8x, clamped to regime_cap=5."""
        # 2000 entry, 1995 stop → 5/2000 = 0.0025 fraction
        # leverage = 0.02 / 0.0025 = 8.0 → clamped to regime_cap=5
        entry = Decimal("2000")
        stop = Decimal("1995")
        regime_cap = Decimal("5.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result == Decimal("5.0")

    def test_moderate_stop_within_bounds(self) -> None:
        """1% stop distance with 2% risk budget → 2x leverage, within [1, 8] bounds."""
        # 2000 entry, 1980 stop → 20/2000 = 0.01 fraction
        # leverage = 0.02 / 0.01 = 2.0
        entry = Decimal("2000")
        stop = Decimal("1980")
        regime_cap = Decimal("8.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result == Decimal("2.0")

    def test_none_stop_returns_regime_cap(self) -> None:
        """No stop loss → unconstrained; return the full regime cap."""
        result = compute_stop_distance_leverage(Decimal("2000"), None, Decimal("6.0"))
        assert result == Decimal("6.0")

    def test_zero_stop_distance_returns_regime_cap(self) -> None:
        """Stop at entry price → distance=0, avoid division by zero, return regime_cap."""
        entry = Decimal("2000")
        stop = Decimal("2000")
        regime_cap = Decimal("4.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result == Decimal("4.0")

    def test_zero_entry_price_returns_regime_cap(self) -> None:
        """Entry price of zero guard; returns regime_cap without dividing by zero."""
        result = compute_stop_distance_leverage(
            Decimal("0"), Decimal("100"), Decimal("3.0")
        )
        assert result == Decimal("3.0")

    def test_custom_risk_budget(self) -> None:
        """1% risk budget with 2% stop → 0.5x → clamped to floor 1.0."""
        # 0.01 / 0.02 = 0.5 → clamped to 1.0
        entry = Decimal("1000")
        stop = Decimal("980")  # 2% distance
        regime_cap = Decimal("5.0")
        result = compute_stop_distance_leverage(
            entry, stop, regime_cap, risk_budget_pct=Decimal("0.01")
        )
        assert result == Decimal("1.0")

    def test_short_position_stop_above_entry(self) -> None:
        """Short stop above entry is handled correctly (abs value of distance)."""
        # Short: entry 2000, stop 2050 → 50/2000 = 0.025 fraction
        # leverage = 0.02 / 0.025 = 0.8 → clamped to 1.0
        entry = Decimal("2000")
        stop = Decimal("2050")
        regime_cap = Decimal("8.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result == Decimal("1.0")

    def test_result_never_exceeds_regime_cap(self) -> None:
        """Result is always ≤ regime_cap regardless of stop distance."""
        # Very tight stop: 0.01% → leverage = 0.02/0.0001 = 200x → must clamp to 8.0
        entry = Decimal("2000")
        stop = Decimal("1999.98")  # 0.01% distance
        regime_cap = Decimal("8.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result <= regime_cap

    def test_result_never_below_floor(self) -> None:
        """Result is always ≥ 1.0 regardless of how wide the stop is."""
        entry = Decimal("2000")
        stop = Decimal("1000")  # 50% away → leverage = 0.04x → floor = 1.0
        regime_cap = Decimal("8.0")
        result = compute_stop_distance_leverage(entry, stop, regime_cap)
        assert result >= Decimal("1.0")


# ---------------------------------------------------------------------------
# compute_effective_leverage_cap
# ---------------------------------------------------------------------------


class TestComputeEffectiveLeverageCap:
    """End-to-end tests for compute_effective_leverage_cap."""

    def test_with_stop_loss_uses_stop_distance(self) -> None:
        """Moderate stop distance within regime cap is returned as-is."""
        # Regime trending_up Route A → cap = 8.0
        # 1% stop: 0.02 / 0.01 = 2.0 → within [1, 8] → result = 2.0
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=Decimal("1980"),
            regime=MarketRegime.TRENDING_UP,
            route=Route.A,
            config=REGIME_LEVERAGE_CONFIG,
        )
        assert result == Decimal("2.0")

    def test_without_stop_loss_returns_regime_cap(self) -> None:
        """No stop loss: result equals the regime leverage cap."""
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=None,
            regime=MarketRegime.RANGING,
            route=Route.A,
            config=REGIME_LEVERAGE_CONFIG,
        )
        # Ranging Route A → cap = 4.0
        assert result == Decimal("4.0")

    def test_hard_cap_enforcement_route_a(self) -> None:
        """Config value that would exceed MAX_LEVERAGE_GLOBAL is clamped."""
        oversize_config: dict = {
            "risk": {
                "regime_leverage": {
                    "route_a": {"trending_up": 99.0},
                    "route_b": {},
                }
            }
        }
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=None,
            regime=MarketRegime.TRENDING_UP,
            route=Route.A,
            config=oversize_config,
        )
        assert result == MAX_LEVERAGE_GLOBAL

    def test_hard_cap_enforcement_route_b(self) -> None:
        """Config value that would exceed MAX_LEVERAGE_ROUTE_B is clamped."""
        oversize_config: dict = {
            "risk": {
                "regime_leverage": {
                    "route_a": {},
                    "route_b": {"trending_up": 99.0},
                }
            }
        }
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=None,
            regime=MarketRegime.TRENDING_UP,
            route=Route.B,
            config=oversize_config,
        )
        assert result == MAX_LEVERAGE_ROUTE_B

    def test_high_volatility_regime_low_cap(self) -> None:
        """High-volatility regime enforces low ceiling even with tight stop."""
        # High volatility Route A → cap = 2.0
        # Very tight stop → stop_distance_leverage would be high, clamped to 2.0
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=Decimal("1999"),  # 0.05% stop → leverage=40 → clamped to 2.0
            regime=MarketRegime.HIGH_VOLATILITY,
            route=Route.A,
            config=REGIME_LEVERAGE_CONFIG,
        )
        assert result == Decimal("2.0")

    def test_route_b_high_volatility_cap(self) -> None:
        """Route B high_volatility cap is 1.5x."""
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=None,
            regime=MarketRegime.HIGH_VOLATILITY,
            route=Route.B,
            config=REGIME_LEVERAGE_CONFIG,
        )
        assert result == Decimal("1.5")

    def test_empty_config_uses_safe_defaults(self) -> None:
        """Missing config returns conservative fallback capped by hard cap."""
        result = compute_effective_leverage_cap(
            entry_price=Decimal("2000"),
            stop_loss=None,
            regime=MarketRegime.TRENDING_UP,
            route=Route.A,
            config=EMPTY_CONFIG,
        )
        assert result == Decimal("3.0")

    def test_result_always_lte_route_a_hard_cap(self) -> None:
        """Across all regimes, Route A result never exceeds MAX_LEVERAGE_GLOBAL."""
        for regime in MarketRegime:
            result = compute_effective_leverage_cap(
                entry_price=Decimal("2000"),
                stop_loss=None,
                regime=regime,
                route=Route.A,
                config=REGIME_LEVERAGE_CONFIG,
            )
            assert result <= MAX_LEVERAGE_GLOBAL, f"Exceeded hard cap for {regime}"

    def test_result_always_lte_route_b_hard_cap(self) -> None:
        """Across all regimes, Route B result never exceeds MAX_LEVERAGE_ROUTE_B."""
        for regime in MarketRegime:
            result = compute_effective_leverage_cap(
                entry_price=Decimal("2000"),
                stop_loss=None,
                regime=regime,
                route=Route.B,
                config=REGIME_LEVERAGE_CONFIG,
            )
            assert result <= MAX_LEVERAGE_ROUTE_B, f"Exceeded hard cap for {regime}"
