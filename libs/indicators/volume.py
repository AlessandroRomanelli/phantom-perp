"""Volume indicators: OBV, VWAP, volume profile."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def obv(
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
) -> NDArray[np.float64]:
    """On-Balance Volume.

    Args:
        closes: Close prices.
        volumes: Volume per period.

    Returns:
        Cumulative OBV array.
    """
    result = np.zeros(len(closes), dtype=np.float64)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result[i] = result[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            result[i] = result[i - 1] - volumes[i]
        else:
            result[i] = result[i - 1]
    return result


def vwap(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Volume-Weighted Average Price (cumulative within session).

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        volumes: Volume per period.

    Returns:
        Cumulative VWAP array.
    """
    typical_price = (highs + lows + closes) / 3.0
    cum_tp_vol = np.cumsum(typical_price * volumes)
    cum_vol = np.cumsum(volumes)
    result = np.where(cum_vol != 0, cum_tp_vol / cum_vol, np.nan)
    return result
