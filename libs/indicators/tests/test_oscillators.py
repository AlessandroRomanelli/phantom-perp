"""Tests for oscillator indicators — ADX contract verification."""

from __future__ import annotations

import numpy as np
import pytest

from libs.indicators.oscillators import adx


def _make_prices(n: int = 50, seed: int = 42) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate synthetic OHLC price arrays from a random walk."""
    rng = np.random.default_rng(seed)
    closes = np.cumsum(rng.standard_normal(n)) + 100.0
    highs = closes + np.abs(rng.standard_normal(n)) * 0.5
    lows = closes - np.abs(rng.standard_normal(n)) * 0.5
    return highs.astype(np.float64), lows.astype(np.float64), closes.astype(np.float64)


def test_adx_no_nan_for_valid_series() -> None:
    """ADX with series length > 2*period must return zero NaN values past 2*period index."""
    highs, lows, closes = _make_prices(50, seed=42)
    period = 14
    result = adx(highs, lows, closes, period=period)

    # Values from index 2*period onward must all be non-NaN
    tail = result[2 * period :]
    nan_count = int(np.isnan(tail).sum())
    assert nan_count == 0, f"Expected 0 NaN values past index {2 * period}, got {nan_count}"


def test_adx_short_series_all_nan() -> None:
    """ADX with series length < period must return all-NaN array."""
    n = 10
    highs = np.ones(n, dtype=np.float64) + 1.0
    lows = np.ones(n, dtype=np.float64)
    closes = np.ones(n, dtype=np.float64) + 0.5
    period = 14

    result = adx(highs, lows, closes, period=period)
    assert np.all(np.isnan(result)), "Expected all NaN for short series"


def test_adx_values_in_range() -> None:
    """All non-NaN ADX values must be in [0, 100]."""
    highs, lows, closes = _make_prices(50, seed=42)
    result = adx(highs, lows, closes, period=14)

    valid = result[~np.isnan(result)]
    assert len(valid) > 0, "Expected at least some valid ADX values"
    assert np.all(valid >= 0.0), f"ADX values below 0: {valid[valid < 0]}"
    assert np.all(valid <= 100.0), f"ADX values above 100: {valid[valid > 100]}"
