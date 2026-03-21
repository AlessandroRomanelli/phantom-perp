"""Liquidation guard — ensures stop-loss triggers before liquidation.

A position's stop-loss must always be between the entry price and the
liquidation price.  If the stop-loss is beyond the liquidation price,
the exchange would liquidate the position before the stop-loss fires,
resulting in a larger loss than intended.
"""

from __future__ import annotations

from decimal import Decimal

from libs.common.models.enums import PositionSide


def stop_is_before_liquidation(
    stop_loss: Decimal,
    liquidation_price: Decimal,
    direction: PositionSide,
) -> bool:
    """Check that the stop-loss would trigger before liquidation.

    For LONG: stop_loss > liquidation_price (stop above liq).
    For SHORT: stop_loss < liquidation_price (stop below liq).

    Args:
        stop_loss: Stop-loss price.
        liquidation_price: Estimated liquidation price.
        direction: LONG or SHORT.

    Returns:
        True if the stop-loss is safely between entry and liquidation.
    """
    if direction == PositionSide.LONG:
        return stop_loss > liquidation_price
    elif direction == PositionSide.SHORT:
        return stop_loss < liquidation_price
    raise ValueError(f"Direction must be LONG or SHORT, got {direction}")
