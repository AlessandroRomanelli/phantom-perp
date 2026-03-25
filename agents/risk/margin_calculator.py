"""Margin and liquidation price estimation for ETH-PERP."""

from __future__ import annotations

from decimal import Decimal

from libs.common.models.enums import PositionSide

# Maintenance margin rate as a fraction of notional.
# Coinbase Advanced uses a tiered system; 1% is a conservative estimate for
# typical ETH-PERP position sizes the system will hold.
MAINTENANCE_MARGIN_RATE = Decimal("0.01")


def compute_initial_margin(
    size: Decimal,
    entry_price: Decimal,
    leverage: Decimal,
) -> Decimal:
    """Compute initial margin required for a new position.

    Args:
        size: Position size in ETH.
        entry_price: Entry price in USDC.
        leverage: Effective leverage for this position.

    Returns:
        Initial margin in USDC.
    """
    if leverage <= 0:
        raise ValueError(f"Leverage must be positive, got {leverage}")
    notional = size * entry_price
    return notional / leverage


def compute_maintenance_margin(
    size: Decimal,
    price: Decimal,
    rate: Decimal = MAINTENANCE_MARGIN_RATE,
) -> Decimal:
    """Compute maintenance margin for a position.

    Args:
        size: Position size in ETH.
        price: Current mark price in USDC.
        rate: Maintenance margin rate (fraction of notional).

    Returns:
        Maintenance margin in USDC.
    """
    return size * price * rate


def compute_liquidation_price(
    entry_price: Decimal,
    leverage: Decimal,
    direction: PositionSide,
    maint_rate: Decimal = MAINTENANCE_MARGIN_RATE,
) -> Decimal:
    """Estimate the liquidation price for a new position.

    Derivation (LONG):
        At liquidation, equity == maintenance margin.
        equity = initial_margin + (liq_price - entry) * size
        maint  = size * liq_price * maint_rate
        Solving: liq_price = entry * (1 - 1/L) / (1 - maint_rate)

    For SHORT:
        liq_price = entry * (1 + 1/L) / (1 + maint_rate)

    Args:
        entry_price: Entry price in USDC.
        leverage: Effective leverage.
        direction: LONG or SHORT.
        maint_rate: Maintenance margin rate.

    Returns:
        Estimated liquidation price in USDC.
    """
    if leverage <= 0:
        raise ValueError(f"Leverage must be positive, got {leverage}")

    inv_lev = Decimal("1") / leverage

    if direction == PositionSide.LONG:
        liq = entry_price * (Decimal("1") - inv_lev) / (Decimal("1") - maint_rate)
    elif direction == PositionSide.SHORT:
        liq = entry_price * (Decimal("1") + inv_lev) / (Decimal("1") + maint_rate)
    else:
        raise ValueError(f"Direction must be LONG or SHORT, got {direction}")

    return liq.quantize(Decimal("0.01"))


def compute_liquidation_distance_pct(
    entry_price: Decimal,
    liquidation_price: Decimal,
    direction: PositionSide,
) -> Decimal:
    """Compute distance from entry to liquidation as a percentage.

    A larger value means more margin of safety.

    Args:
        entry_price: Entry price in USDC.
        liquidation_price: Estimated liquidation price.
        direction: LONG or SHORT.

    Returns:
        Distance as a positive percentage (e.g. Decimal("19.5") = 19.5%).
    """
    if entry_price == 0:
        return Decimal("0")

    if direction == PositionSide.LONG:
        distance = (entry_price - liquidation_price) / entry_price
    else:
        distance = (liquidation_price - entry_price) / entry_price

    return (distance * Decimal("100")).quantize(Decimal("0.01"))
