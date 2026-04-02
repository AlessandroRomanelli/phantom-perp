"""Position sizing based on portfolio equity, risk limits, and conviction.

Computes the maximum allowable position size given all constraints, then
scales by the trade idea's conviction score.
"""

from __future__ import annotations

from decimal import Decimal

from libs.common.models.position import PerpPosition
from libs.common.utils import round_size

from agents.risk.limits import RiskLimits


def compute_position_size(
    entry_price: Decimal,
    conviction: float,
    equity: Decimal,
    used_margin: Decimal,
    existing_positions: list[PerpPosition],
    limits: RiskLimits,
    min_order_size: Decimal = Decimal("0.0001"),
    effective_leverage: Decimal | None = None,
) -> Decimal:
    """Compute position size respecting all risk constraints.

    Takes the minimum across all constraints, then scales by conviction.

    Args:
        entry_price: Expected entry price in USDC.
        conviction: Signal conviction (0–1), scales position proportionally.
        equity: Portfolio equity in USDC.
        used_margin: Currently used margin in USDC.
        existing_positions: Open positions in this portfolio.
        limits: Per-portfolio risk limits.
        effective_leverage: Dynamic leverage cap (regime + stop-distance).
            When provided, overrides limits.max_leverage for sizing steps 3 and 4.
            When None, falls back to limits.max_leverage (backward-compatible).

    Returns:
        Position size in ETH (rounded down to valid increment).
        Returns Decimal("0") if no valid size exists.
    """
    if entry_price <= 0 or equity <= 0:
        return Decimal("0")

    # Effective leverage: dynamic cap when provided, otherwise static limit.
    lev = effective_leverage if effective_leverage is not None else limits.max_leverage

    # 1. Absolute max from notional cap (instrument-agnostic)
    max_from_notional = limits.max_position_notional_usdc / entry_price

    # 2. Max from equity percentage — interpreted as a margin budget, scaled by leverage.
    # e.g. 40% equity at 2x lev → max notional = 80% of equity (40% deployed as margin).
    max_margin_budget = equity * limits.max_position_pct_equity / Decimal("100")
    max_from_equity = max_margin_budget * lev / entry_price

    # 3. Max from leverage constraint (total portfolio leverage)
    existing_notional = sum(
        p.size * p.mark_price for p in existing_positions if p.is_open
    )
    max_total_notional = equity * lev
    max_additional_notional = max_total_notional - existing_notional
    max_from_leverage = max(Decimal("0"), max_additional_notional / entry_price)

    # 4. Max from margin utilization
    max_total_margin = equity * limits.max_margin_utilization_pct / Decimal("100")
    available_margin = max(Decimal("0"), max_total_margin - used_margin)
    # Margin required = notional / leverage → max notional = available * leverage
    max_from_margin = available_margin * lev / entry_price

    # Take the most restrictive constraint
    max_size = min(max_from_notional, max_from_equity, max_from_leverage, max_from_margin)

    if max_size < min_order_size:
        return Decimal("0")

    # Scale by conviction using convex (power-function) scaling
    conviction_scaled = Decimal(str(conviction ** limits.conviction_power))
    size = max_size * conviction_scaled

    return round_size(size, min_order_size)
