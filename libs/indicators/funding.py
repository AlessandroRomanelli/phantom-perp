"""Funding rate analytics for hourly settlement data."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from libs.indicators.moving_averages import sma


def funding_rate_zscore(
    rates: NDArray[np.float64],
    lookback: int = 168,
) -> NDArray[np.float64]:
    """Z-score of the current funding rate relative to recent history.

    Args:
        rates: Array of hourly funding rates.
        lookback: Number of hours for mean/std calculation (default 168 = 1 week).

    Returns:
        Z-score array.
    """
    result = np.full(len(rates), np.nan, dtype=np.float64)
    for i in range(lookback, len(rates)):
        window = rates[i - lookback : i]
        mean = np.mean(window)
        std = np.std(window, ddof=1)
        if std > 0:
            result[i] = (rates[i] - mean) / std
    return result


def cumulative_funding(
    rates: NDArray[np.float64],
    window_hours: int = 24,
) -> NDArray[np.float64]:
    """Rolling cumulative funding rate over a window.

    Args:
        rates: Array of hourly funding rates.
        window_hours: Rolling window in hours (default 24).

    Returns:
        Rolling sum of funding rates.
    """
    result = np.full(len(rates), np.nan, dtype=np.float64)
    cumsum = np.cumsum(rates)
    result[window_hours - 1 :] = (
        cumsum[window_hours - 1 :] - np.concatenate([[0], cumsum[:-window_hours]])
    )
    return result


def predicted_funding_ema(
    rates: NDArray[np.float64],
    period: int = 8,
) -> NDArray[np.float64]:
    """Simple EMA-based funding rate prediction.

    Args:
        rates: Array of hourly funding rates.
        period: EMA smoothing period.

    Returns:
        EMA of funding rates (can be used as next-hour prediction).
    """
    from libs.indicators.moving_averages import ema

    return ema(rates, period)
