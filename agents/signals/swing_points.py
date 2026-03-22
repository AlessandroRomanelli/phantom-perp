"""Swing high/low detection for structure-aware stops.

Extracted from momentum strategy's inline implementation to a shared
utility. Finds the most recent swing point (local min/max) within a
lookback window.

Follows the established function-based utility pattern from funding_filter.py:
no class state, no side effects.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def find_swing_low(
    lows: NDArray[np.float64],
    lookback: int = 20,
    order: int = 3,
) -> float | None:
    """Find the most recent swing low within the lookback window.

    A swing low is a point where the low is less than or equal to the
    `order` bars on each side.

    Args:
        lows: Array of low prices.
        lookback: Number of bars to search backwards.
        order: Minimum bars on each side of the swing.

    Returns:
        The swing low price, or None if not found.
    """
    if len(lows) < lookback:
        search = lows
    else:
        search = lows[-lookback:]

    if len(search) < 2 * order + 1:
        return None

    for i in range(len(search) - 1 - order, order - 1, -1):
        is_swing = True
        for j in range(1, order + 1):
            if search[i] > search[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, min(order + 1, len(search) - i)):
                if search[i] > search[i + j]:
                    is_swing = False
                    break
        if is_swing:
            return float(search[i])
    return None


def find_swing_high(
    highs: NDArray[np.float64],
    lookback: int = 20,
    order: int = 3,
) -> float | None:
    """Find the most recent swing high within the lookback window.

    A swing high is a point where the high is greater than or equal to
    the `order` bars on each side.

    Args:
        highs: Array of high prices.
        lookback: Number of bars to search backwards.
        order: Minimum bars on each side of the swing.

    Returns:
        The swing high price, or None if not found.
    """
    if len(highs) < lookback:
        search = highs
    else:
        search = highs[-lookback:]

    if len(search) < 2 * order + 1:
        return None

    for i in range(len(search) - 1 - order, order - 1, -1):
        is_swing = True
        for j in range(1, order + 1):
            if search[i] < search[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, min(order + 1, len(search) - i)):
                if search[i] < search[i + j]:
                    is_swing = False
                    break
        if is_swing:
            return float(search[i])
    return None
