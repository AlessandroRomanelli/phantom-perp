"""Shared utility functions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from libs.common.constants import MIN_ORDER_SIZE, TICK_SIZE


def utc_now() -> datetime:
    """Return the current UTC datetime (always timezone-aware)."""
    return datetime.now(UTC)


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with an optional prefix.

    Args:
        prefix: Optional prefix (e.g., 'sig', 'ord').

    Returns:
        A string like 'ord-550e8400-e29b-41d4-a716-446655440000'.
    """
    uid = str(uuid.uuid4())
    return f"{prefix}-{uid}" if prefix else uid


def round_to_tick(price: Decimal, tick_size: Decimal = TICK_SIZE) -> Decimal:
    """Round a price down to the nearest tick size.

    Args:
        price: Raw price to round.
        tick_size: Minimum price increment.

    Returns:
        Price rounded down to the nearest tick.
    """
    return (price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size


def round_size(size: Decimal, min_size: Decimal = MIN_ORDER_SIZE) -> Decimal:
    """Round an order size down to the nearest valid increment.

    Args:
        size: Raw order size in ETH.
        min_size: Minimum order size (lot size).

    Returns:
        Size rounded down to the nearest lot.
    """
    return (size / min_size).to_integral_value(rounding=ROUND_DOWN) * min_size


def bps_to_decimal(bps: float) -> Decimal:
    """Convert basis points to a decimal multiplier.

    Args:
        bps: Value in basis points (e.g., 25 = 0.25%).

    Returns:
        Decimal multiplier (e.g., Decimal('0.0025')).
    """
    return Decimal(str(bps)) / Decimal("10000")


def pct_change(old: Decimal, new: Decimal) -> Decimal:
    """Calculate percentage change from old to new.

    Args:
        old: Original value.
        new: New value.

    Returns:
        Percentage change as Decimal (e.g., Decimal('5.25') for +5.25%).
    """
    if old == 0:
        return Decimal("0")
    return ((new - old) / old * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_fee(notional_usdc: Decimal, is_maker: bool) -> Decimal:
    """Compute the trading fee for a given notional.

    Args:
        notional_usdc: Order notional value in USDC.
        is_maker: True if limit order (maker rate), False if market (taker).

    Returns:
        Fee amount in USDC.
    """
    from libs.common.constants import FEE_MAKER, FEE_TAKER

    rate = FEE_MAKER if is_maker else FEE_TAKER
    return (notional_usdc * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
