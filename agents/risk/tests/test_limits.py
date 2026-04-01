"""Tests for per-portfolio risk limit loading and hard-cap enforcement."""

from decimal import Decimal

from libs.common.constants import (
    MAX_LEVERAGE_GLOBAL,
    MAX_LEVERAGE_ROUTE_B,
    ROUTE_A_DAILY_LOSS_KILL_PCT,
    ROUTE_A_MAX_DRAWDOWN_PCT,
    ROUTE_A_MAX_POSITION_PCT_EQUITY,
    ROUTE_A_MIN_LIQUIDATION_DISTANCE_PCT,
    ROUTE_B_MAX_DAILY_LOSS_PCT,
    ROUTE_B_MAX_DRAWDOWN_PCT,
    ROUTE_B_MIN_LIQUIDATION_DISTANCE_PCT,
)
from libs.common.models.enums import Route

from agents.risk.limits import limits_for_route

# Mimics the relevant section from configs/default.yaml
DEFAULT_CONFIG = {
    "risk": {
        "route_a": {
            "max_leverage": 5.0,
            "max_position_notional_usdc": 6000,
            "max_position_pct_equity": 40.0,
            "max_margin_utilization_pct": 70.0,
            "min_liquidation_distance_pct": 8.0,
            "max_daily_loss_pct": 10.0,
            "max_drawdown_pct": 25.0,
            "stop_loss_required": True,
            "max_concurrent_positions": 3,
            "max_funding_cost_per_day_usdc": 20,
        },
        "route_b": {
            "max_leverage": 3.0,
            "max_position_notional_usdc": 16000,
            "max_position_pct_equity": 25.0,
            "max_margin_utilization_pct": 50.0,
            "min_liquidation_distance_pct": 15.0,
            "max_daily_loss_pct": 5.0,
            "max_drawdown_pct": 15.0,
            "stop_loss_required": True,
            "max_concurrent_positions": 3,
            "max_funding_cost_per_day_usdc": 100,
        },
    }
}


class TestLimitsForRoute:
    def test_route_a_defaults(self) -> None:
        limits = limits_for_route(Route.A, DEFAULT_CONFIG)
        assert limits.max_leverage == Decimal("5.0")
        assert limits.max_position_notional_usdc == Decimal("6000")
        assert limits.max_position_pct_equity == Decimal("40.0")
        assert limits.min_liquidation_distance_pct == Decimal("8.0")
        assert limits.max_daily_loss_pct == Decimal("10.0")
        assert limits.max_drawdown_pct == Decimal("25.0")
        assert limits.stop_loss_required is True
        assert limits.max_concurrent_positions == 3

    def test_route_b_defaults(self) -> None:
        limits = limits_for_route(Route.B, DEFAULT_CONFIG)
        assert limits.max_leverage == Decimal("3.0")
        assert limits.max_position_notional_usdc == Decimal("16000")
        assert limits.max_position_pct_equity == Decimal("25.0")
        assert limits.min_liquidation_distance_pct == Decimal("15.0")
        assert limits.max_daily_loss_pct == Decimal("5.0")
        assert limits.max_drawdown_pct == Decimal("15.0")
        assert limits.max_funding_cost_per_day_usdc == Decimal("100")

    def test_a_and_b_have_different_limits(self) -> None:
        a = limits_for_route(Route.A, DEFAULT_CONFIG)
        b = limits_for_route(Route.B, DEFAULT_CONFIG)
        assert a.max_leverage > b.max_leverage
        assert a.min_liquidation_distance_pct < b.min_liquidation_distance_pct
        assert a.max_daily_loss_pct > b.max_daily_loss_pct

    def test_config_cannot_exceed_hard_leverage_cap_a(self) -> None:
        """Config sets 10x leverage, but hard cap for A is 5x."""
        config = {
            "risk": {"route_a": {"max_leverage": 10.0}},
        }
        limits = limits_for_route(Route.A, config)
        assert limits.max_leverage == MAX_LEVERAGE_GLOBAL

    def test_config_cannot_exceed_hard_leverage_cap_b(self) -> None:
        """Config sets 5x leverage, but hard cap for B is 3x."""
        config = {
            "risk": {"route_b": {"max_leverage": 5.0}},
        }
        limits = limits_for_route(Route.B, config)
        assert limits.max_leverage == MAX_LEVERAGE_ROUTE_B

    def test_config_cannot_lower_liquidation_distance_floor(self) -> None:
        """Config sets 3% liq distance, but hard floor for A is 8%."""
        config = {
            "risk": {"route_a": {"min_liquidation_distance_pct": 3.0}},
        }
        limits = limits_for_route(Route.A, config)
        assert limits.min_liquidation_distance_pct == ROUTE_A_MIN_LIQUIDATION_DISTANCE_PCT

    def test_config_can_tighten_limits(self) -> None:
        """Config sets tighter limits than the hard caps — should be honored."""
        config = {
            "risk": {
                "route_a": {
                    "max_leverage": 2.0,
                    "max_daily_loss_pct": 3.0,
                    "min_liquidation_distance_pct": 20.0,
                }
            },
        }
        limits = limits_for_route(Route.A, config)
        assert limits.max_leverage == Decimal("2.0")
        assert limits.max_daily_loss_pct == Decimal("3.0")
        assert limits.min_liquidation_distance_pct == Decimal("20.0")

    def test_empty_config_uses_hard_caps(self) -> None:
        limits = limits_for_route(Route.A, {})
        assert limits.max_leverage == MAX_LEVERAGE_GLOBAL
        assert limits.min_liquidation_distance_pct == ROUTE_A_MIN_LIQUIDATION_DISTANCE_PCT
