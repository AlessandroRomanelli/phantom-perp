"""Per-portfolio risk limits with hard-coded safety caps.

Config values from YAML can tighten limits but never loosen them beyond
the non-negotiable constants defined in libs/common/constants.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from libs.common.constants import (
    MAX_LEVERAGE_GLOBAL,
    MAX_LEVERAGE_PORTFOLIO_B,
    PORTFOLIO_A_DAILY_LOSS_KILL_PCT,
    PORTFOLIO_A_MAX_DRAWDOWN_PCT,
    PORTFOLIO_A_MAX_POSITION_PCT_EQUITY,
    PORTFOLIO_A_MIN_LIQUIDATION_DISTANCE_PCT,
    PORTFOLIO_B_MAX_DAILY_LOSS_PCT,
    PORTFOLIO_B_MAX_DRAWDOWN_PCT,
    PORTFOLIO_B_MIN_LIQUIDATION_DISTANCE_PCT,
)
from libs.common.models.enums import PortfolioTarget


@dataclass(frozen=True)
class RiskLimits:
    """Risk limits for a single portfolio.

    All percentage values are in points (e.g. 40.0 means 40%).
    """

    max_leverage: Decimal
    max_position_size_eth: Decimal
    max_position_pct_equity: Decimal
    max_margin_utilization_pct: Decimal
    min_liquidation_distance_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    stop_loss_required: bool
    max_concurrent_positions: int
    max_funding_cost_per_day_usdc: Decimal


def _d(value: object, default: str) -> Decimal:
    """Convert a config value to Decimal, falling back to default."""
    return Decimal(str(value)) if value is not None else Decimal(default)


def limits_for_portfolio(
    target: PortfolioTarget,
    config: dict[str, Any],
) -> RiskLimits:
    """Build RiskLimits from YAML config, enforcing hard-coded safety caps.

    Config values can be MORE restrictive than the hard caps but never LESS.
    For max-type limits: result = min(config, hard_cap).
    For min-type limits (liquidation distance): result = max(config, hard_floor).

    Args:
        target: Which portfolio these limits are for.
        config: Parsed YAML config (the full root dict).

    Returns:
        RiskLimits with hard caps enforced.
    """
    risk = config.get("risk", {})

    if target == PortfolioTarget.A:
        section = risk.get("portfolio_a", {})
        hard_leverage = MAX_LEVERAGE_GLOBAL
        hard_liq_dist = PORTFOLIO_A_MIN_LIQUIDATION_DISTANCE_PCT
        hard_daily_loss = PORTFOLIO_A_DAILY_LOSS_KILL_PCT
        hard_drawdown = PORTFOLIO_A_MAX_DRAWDOWN_PCT
        hard_pos_pct = PORTFOLIO_A_MAX_POSITION_PCT_EQUITY
    else:
        section = risk.get("portfolio_b", {})
        hard_leverage = MAX_LEVERAGE_PORTFOLIO_B
        hard_liq_dist = PORTFOLIO_B_MIN_LIQUIDATION_DISTANCE_PCT
        hard_daily_loss = PORTFOLIO_B_MAX_DAILY_LOSS_PCT
        hard_drawdown = PORTFOLIO_B_MAX_DRAWDOWN_PCT
        hard_pos_pct = Decimal("25.0")

    cfg_leverage = _d(section.get("max_leverage"), str(hard_leverage))
    cfg_liq_dist = _d(section.get("min_liquidation_distance_pct"), str(hard_liq_dist))
    cfg_daily_loss = _d(section.get("max_daily_loss_pct"), str(hard_daily_loss))
    cfg_drawdown = _d(section.get("max_drawdown_pct"), str(hard_drawdown))
    cfg_pos_pct = _d(section.get("max_position_pct_equity"), str(hard_pos_pct))

    return RiskLimits(
        max_leverage=min(cfg_leverage, hard_leverage),
        max_position_size_eth=_d(section.get("max_position_size_eth"), "3.0"),
        max_position_pct_equity=min(cfg_pos_pct, hard_pos_pct),
        max_margin_utilization_pct=_d(section.get("max_margin_utilization_pct"), "70.0"),
        min_liquidation_distance_pct=max(cfg_liq_dist, hard_liq_dist),
        max_daily_loss_pct=min(cfg_daily_loss, hard_daily_loss),
        max_drawdown_pct=min(cfg_drawdown, hard_drawdown),
        stop_loss_required=section.get("stop_loss_required", True),
        max_concurrent_positions=int(section.get("max_concurrent_positions", 3)),
        max_funding_cost_per_day_usdc=_d(
            section.get("max_funding_cost_per_day_usdc"), "20"
        ),
    )
