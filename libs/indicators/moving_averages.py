"""Moving average indicators: SMA, EMA, VWMA."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def sma(values: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Simple Moving Average.

    Args:
        values: Input price array.
        period: Lookback period.

    Returns:
        Array of same length with NaN for the first (period - 1) elements.
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    if len(values) < period:
        return np.full_like(values, np.nan)

    result = np.full_like(values, np.nan, dtype=np.float64)
    cumsum = np.cumsum(values)
    result[period - 1 :] = (cumsum[period - 1 :] - np.concatenate([[0], cumsum[:-period]])) / period
    return result


def ema(values: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Exponential Moving Average.

    Uses the standard multiplier: 2 / (period + 1).
    The first value is seeded with the SMA of the first `period` values.

    Args:
        values: Input price array.
        period: Lookback period.

    Returns:
        Array of same length with NaN for the first (period - 2) elements.
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    if len(values) < period:
        return np.full_like(values, np.nan)

    result = np.full_like(values, np.nan, dtype=np.float64)
    multiplier = 2.0 / (period + 1)

    # Seed with SMA of the first `period` values
    result[period - 1] = np.mean(values[:period])

    for i in range(period, len(values)):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]

    return result


def vwma(
    prices: NDArray[np.float64],
    volumes: NDArray[np.float64],
    period: int,
) -> NDArray[np.float64]:
    """Volume-Weighted Moving Average.

    Args:
        prices: Input price array.
        volumes: Input volume array (same length as prices).
        period: Lookback period.

    Returns:
        Array of same length with NaN for the first (period - 1) elements.
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    if len(prices) != len(volumes):
        raise ValueError("prices and volumes must have the same length")
    if len(prices) < period:
        return np.full_like(prices, np.nan)

    pv = prices * volumes
    result = np.full_like(prices, np.nan, dtype=np.float64)

    pv_cumsum = np.cumsum(pv)
    v_cumsum = np.cumsum(volumes)

    pv_sum = pv_cumsum[period - 1 :] - np.concatenate([[0], pv_cumsum[:-period]])
    v_sum = v_cumsum[period - 1 :] - np.concatenate([[0], v_cumsum[:-period]])

    # Avoid division by zero
    mask = v_sum != 0
    result[period - 1 :][mask] = pv_sum[mask] / v_sum[mask]

    return result
