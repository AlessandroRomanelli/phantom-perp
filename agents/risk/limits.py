"""Per-route risk limits with hard-coded safety caps.

Config values from YAML can tighten limits but never loosen them beyond
the non-negotiable constants defined in libs/common/constants.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

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


@dataclass(frozen=True)
class RiskLimits:
    """Risk limits for a single route.

    All percentage values are in points (e.g. 40.0 means 40%).
    """

    max_leverage: Decimal
    max_position_notional_usdc: Decimal
    max_position_pct_equity: Decimal
    max_margin_utilization_pct: Decimal
    min_liquidation_distance_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    stop_loss_required: bool
    max_concurrent_positions: int
    max_positions_per_instrument: int
    max_funding_cost_per_day_usdc: Decimal
    conviction_power: float = 2.0
    min_expected_move_pct: Decimal = Decimal("0.005")
    correlation_enabled: bool = True
    max_net_directional_exposure_pct: Decimal = Decimal("100.0")
    hwm_drawdown_enabled: bool = True


def _d(value: object, default: str) -> Decimal:
    """Convert a config value to Decimal, falling back to default."""
    return Decimal(str(value)) if value is not None else Decimal(default)


def limits_for_route(
    target: Route,
    config: dict[str, Any],
) -> RiskLimits:
    """Build RiskLimits from YAML config, enforcing hard-coded safety caps.

    Config values can be MORE restrictive than the hard caps but never LESS.
    For max-type limits: result = min(config, hard_cap).
    For min-type limits (liquidation distance): result = max(config, hard_floor).

    Args:
        target: Which route these limits are for.
        config: Parsed YAML config (the full root dict).

    Returns:
        RiskLimits with hard caps enforced.
    """
    risk = config.get("risk", {})

    if target == Route.A:
        section = risk.get("route_a", {})
        hard_leverage = MAX_LEVERAGE_GLOBAL
        hard_liq_dist = ROUTE_A_MIN_LIQUIDATION_DISTANCE_PCT
        hard_daily_loss = ROUTE_A_DAILY_LOSS_KILL_PCT
        hard_drawdown = ROUTE_A_MAX_DRAWDOWN_PCT
        hard_pos_pct = ROUTE_A_MAX_POSITION_PCT_EQUITY
    else:
        section = risk.get("route_b", {})
        hard_leverage = MAX_LEVERAGE_ROUTE_B
        hard_liq_dist = ROUTE_B_MIN_LIQUIDATION_DISTANCE_PCT
        hard_daily_loss = ROUTE_B_MAX_DAILY_LOSS_PCT
        hard_drawdown = ROUTE_B_MAX_DRAWDOWN_PCT
        hard_pos_pct = Decimal("25.0")

    cfg_leverage = _d(section.get("max_leverage"), str(hard_leverage))
    cfg_liq_dist = _d(section.get("min_liquidation_distance_pct"), str(hard_liq_dist))
    cfg_daily_loss = _d(section.get("max_daily_loss_pct"), str(hard_daily_loss))
    cfg_drawdown = _d(section.get("max_drawdown_pct"), str(hard_drawdown))
    cfg_pos_pct = _d(section.get("max_position_pct_equity"), str(hard_pos_pct))

    return RiskLimits(
        max_leverage=min(cfg_leverage, hard_leverage),
        max_position_notional_usdc=_d(section.get("max_position_notional_usdc"), "6000"),
        max_position_pct_equity=min(cfg_pos_pct, hard_pos_pct),
        max_margin_utilization_pct=_d(section.get("max_margin_utilization_pct"), "70.0"),
        min_liquidation_distance_pct=max(cfg_liq_dist, hard_liq_dist),
        max_daily_loss_pct=min(cfg_daily_loss, hard_daily_loss),
        max_drawdown_pct=min(cfg_drawdown, hard_drawdown),
        stop_loss_required=section.get("stop_loss_required", True),
        max_concurrent_positions=int(section.get("max_concurrent_positions", 3)),
        max_positions_per_instrument=int(section.get("max_positions_per_instrument", 1)),
        max_funding_cost_per_day_usdc=_d(
            section.get("max_funding_cost_per_day_usdc"), "20"
        ),
        conviction_power=float(risk.get("global", {}).get("conviction_power", 2.0)),
        min_expected_move_pct=_d(
            risk.get("global", {}).get("min_expected_move_pct"), "0.005"
        ),
        correlation_enabled=bool(section.get("correlation_enabled", True)),
        max_net_directional_exposure_pct=_d(
            section.get("max_net_directional_exposure_pct"), "100.0"
        ),
        hwm_drawdown_enabled=bool(section.get("hwm_drawdown_enabled", True)),
    )
