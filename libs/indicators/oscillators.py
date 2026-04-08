"""Oscillator indicators: RSI, MACD, Stochastic."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from libs.indicators.moving_averages import ema


def rsi(values: NDArray[np.float64], period: int = 14) -> NDArray[np.float64]:
    """Relative Strength Index.

    Uses the Wilder smoothing method (exponential moving average of gains/losses).

    Args:
        values: Input price array.
        period: RSI period (default 14).

    Returns:
        Array of same length with NaN for the first `period` elements.
        RSI values are in the range [0, 100].
    """
    if period < 1:
        raise ValueError(f"Period must be >= 1, got {period}")
    if len(values) < period + 1:
        return np.full_like(values, np.nan)

    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    result = np.full(len(values), np.nan, dtype=np.float64)

    # Seed with simple average of first `period` changes
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder smoothing
    alpha = 1.0 / period
    for i in range(period, len(deltas)):
        avg_gain = avg_gain * (1 - alpha) + gains[i] * alpha
        avg_loss = avg_loss * (1 - alpha) + losses[i] * alpha

        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


@dataclass(frozen=True)
class MACDResult:
    """MACD indicator output."""

    macd_line: NDArray[np.float64]
    signal_line: NDArray[np.float64]
    histogram: NDArray[np.float64]


def macd(
    values: NDArray[np.float64],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """Moving Average Convergence Divergence.

    Args:
        values: Input price array.
        fast_period: Fast EMA period (default 12).
        slow_period: Slow EMA period (default 26).
        signal_period: Signal line EMA period (default 9).

    Returns:
        MACDResult with macd_line, signal_line, and histogram arrays.
    """
    fast_ema = ema(values, fast_period)
    slow_ema = ema(values, slow_period)
    macd_line = fast_ema - slow_ema

    # Signal line is EMA of the MACD line
    # Only compute over the valid (non-NaN) portion
    valid_start = slow_period - 1  # first valid MACD value
    signal_line = np.full_like(values, np.nan, dtype=np.float64)

    if len(values) > valid_start + signal_period:
        valid_macd = macd_line[valid_start:]
        signal_ema = ema(valid_macd, signal_period)
        signal_line[valid_start:] = signal_ema

    histogram = macd_line - signal_line

    return MACDResult(
        macd_line=macd_line,
        signal_line=signal_line,
        histogram=histogram,
    )


@dataclass(frozen=True)
class StochasticResult:
    """Stochastic oscillator output."""

    k: NDArray[np.float64]
    d: NDArray[np.float64]


def stochastic(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    k_period: int = 14,
    d_period: int = 3,
) -> StochasticResult:
    """Stochastic oscillator (%K and %D).

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        k_period: %K lookback period (default 14).
        d_period: %D smoothing period (default 3).

    Returns:
        StochasticResult with k and d arrays.
    """
    n = len(closes)
    k = np.full(n, np.nan, dtype=np.float64)

    for i in range(k_period - 1, n):
        window_high = np.max(highs[i - k_period + 1 : i + 1])
        window_low = np.min(lows[i - k_period + 1 : i + 1])
        if window_high == window_low:
            k[i] = 50.0  # Midpoint when range is zero
        else:
            k[i] = (closes[i] - window_low) / (window_high - window_low) * 100.0

    # %D is SMA of %K
    d = np.full(n, np.nan, dtype=np.float64)
    for i in range(k_period - 1 + d_period - 1, n):
        window = k[i - d_period + 1 : i + 1]
        if not np.any(np.isnan(window)):
            d[i] = np.mean(window)

    return StochasticResult(k=k, d=d)


def adx(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Average Directional Index (Wilder's ADX).

    Measures trend strength on a 0-100 scale. Values above 25 typically
    indicate a trending market; below 20 indicates ranging.

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        period: Smoothing period (default 14).

    Returns:
        ADX array with NaN for the first ~2*period elements.
    """
    n = len(closes)
    if n < period + 1:
        return np.full(n, np.nan, dtype=np.float64)

    # True Range
    tr = np.full(n, np.nan, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # Directional movement
    plus_dm = np.zeros(n, dtype=np.float64)
    minus_dm = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # Wilder smoothing for TR, +DM, -DM
    alpha = 1.0 / period
    smoothed_tr = np.full(n, np.nan, dtype=np.float64)
    smoothed_plus = np.full(n, np.nan, dtype=np.float64)
    smoothed_minus = np.full(n, np.nan, dtype=np.float64)

    smoothed_tr[period] = np.sum(tr[1 : period + 1])
    smoothed_plus[period] = np.sum(plus_dm[1 : period + 1])
    smoothed_minus[period] = np.sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - smoothed_tr[i - 1] * alpha + tr[i]
        smoothed_plus[i] = smoothed_plus[i - 1] - smoothed_plus[i - 1] * alpha + plus_dm[i]
        smoothed_minus[i] = smoothed_minus[i - 1] - smoothed_minus[i - 1] * alpha + minus_dm[i]

    # +DI and -DI
    plus_di = np.full(n, np.nan, dtype=np.float64)
    minus_di = np.full(n, np.nan, dtype=np.float64)
    for i in range(period, n):
        if smoothed_tr[i] > 0:
            plus_di[i] = 100.0 * smoothed_plus[i] / smoothed_tr[i]
            minus_di[i] = 100.0 * smoothed_minus[i] / smoothed_tr[i]

    # DX
    dx = np.full(n, np.nan, dtype=np.float64)
    for i in range(period, n):
        if not np.isnan(plus_di[i]) and not np.isnan(minus_di[i]):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / di_sum

    # ADX = smoothed DX
    result = np.full(n, np.nan, dtype=np.float64)
    # Seed ADX with mean of first `period` valid DX values
    first_valid = period
    valid_dx = dx[first_valid : first_valid + period]
    valid_mask = ~np.isnan(valid_dx)
    if np.sum(valid_mask) >= 1:
        seed_idx = first_valid + period - 1
        if seed_idx < n:
            result[seed_idx] = np.nanmean(valid_dx)
            for i in range(seed_idx + 1, n):
                if not np.isnan(dx[i]):
                    result[i] = result[i - 1] * (1 - alpha) + dx[i] * alpha
                else:
                    result[i] = result[i - 1]

    return result
