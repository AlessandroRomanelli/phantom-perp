"""Volatility indicators: ATR, Bollinger Bands, realized volatility."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from libs.indicators.moving_averages import sma


def atr(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Average True Range.

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        period: ATR smoothing period.

    Returns:
        Array with ATR values (NaN for first `period` elements).
    """
    n = len(closes)
    tr = np.full(n, np.nan, dtype=np.float64)
    tr[0] = highs[0] - lows[0]

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    result = np.full(n, np.nan, dtype=np.float64)
    result[period - 1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, n):
        result[i] = result[i - 1] * (1 - alpha) + tr[i] * alpha

    return result


@dataclass(frozen=True)
class BollingerBandsResult:
    """Bollinger Bands output."""

    upper: NDArray[np.float64]
    middle: NDArray[np.float64]
    lower: NDArray[np.float64]
    bandwidth: NDArray[np.float64]


def bollinger_bands(
    values: NDArray[np.float64],
    period: int = 20,
    num_std: float = 2.0,
) -> BollingerBandsResult:
    """Bollinger Bands.

    Args:
        values: Input price array.
        period: SMA period.
        num_std: Number of standard deviations for bands.

    Returns:
        BollingerBandsResult with upper, middle, lower, and bandwidth.
    """
    middle = sma(values, period)
    n = len(values)
    std = np.full(n, np.nan, dtype=np.float64)

    for i in range(period - 1, n):
        std[i] = np.std(values[i - period + 1 : i + 1], ddof=1)

    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = np.where(middle != 0, (upper - lower) / middle, np.nan)

    return BollingerBandsResult(
        upper=upper,
        middle=middle,
        lower=lower,
        bandwidth=bandwidth,
    )


def realized_volatility(
    values: NDArray[np.float64],
    period: int = 24,
    annualize_factor: float = 8760.0,
) -> NDArray[np.float64]:
    """Realized volatility from log returns.

    Args:
        values: Price series.
        period: Rolling window for stdev of log returns.
        annualize_factor: Annualization factor (8760 for hourly data = 24*365).

    Returns:
        Annualized realized volatility array.
    """
    log_returns = np.full(len(values), np.nan, dtype=np.float64)
    log_returns[1:] = np.log(values[1:] / values[:-1])

    result = np.full(len(values), np.nan, dtype=np.float64)
    for i in range(period, len(values)):
        window = log_returns[i - period + 1 : i + 1]
        if not np.any(np.isnan(window)):
            result[i] = np.std(window, ddof=1) * np.sqrt(annualize_factor)

    return result
