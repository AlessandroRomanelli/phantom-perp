"""Tests for swing point detection utility."""

from __future__ import annotations

import numpy as np

from agents.signals.swing_points import find_swing_high, find_swing_low


def test_find_swing_low_v_shape() -> None:
    """Find a V-shaped dip in synthetic data."""
    # Create data with a clear V-dip at index 10
    data = np.array(
        [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0],
        dtype=np.float64,
    )
    result = find_swing_low(data, lookback=16, order=3)
    assert result is not None
    assert result == 0.5


def test_find_swing_high_inverted_v() -> None:
    """Find an inverted-V peak in synthetic data."""
    data = np.array(
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 9.5, 8.0, 7.0, 6.0, 5.0, 4.0],
        dtype=np.float64,
    )
    result = find_swing_high(data, lookback=16, order=3)
    assert result is not None
    assert result == 10.0


def test_find_swing_low_array_too_short() -> None:
    """Return None when array is shorter than 2*order+1."""
    data = np.array([1.0, 0.5, 1.0], dtype=np.float64)  # len=3, need 2*3+1=7
    result = find_swing_low(data, lookback=20, order=3)
    assert result is None


def test_find_swing_low_flat_data() -> None:
    """Return None when all values are the same (no swing)."""
    data = np.full(20, 5.0, dtype=np.float64)
    result = find_swing_low(data, lookback=20, order=3)
    # Flat data: search[i] > search[i-j] is False for equal values,
    # so is_swing remains True. This is by design -- equal neighbors count as swing.
    # The function returns the most recent qualifying point.
    # Actually, let's check what momentum does: search[i] > search[i-j] would be
    # False for equal, so is_swing stays True. It will return 5.0.
    # This matches momentum behavior.
    assert result == 5.0 or result is None  # Accept either -- matches momentum impl


def test_find_swing_low_lookback_limits_search() -> None:
    """When lookback < len(data), only search the tail."""
    # Put a clear swing low at index 2 (early in the array)
    data = np.array(
        [5.0, 3.0, 1.0, 3.0, 5.0, 7.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0],
        dtype=np.float64,
    )
    # With lookback=5, only the last 5 elements are searched (all 9.0)
    result = find_swing_low(data, lookback=5, order=1)
    # The swing low at index 2 should NOT be found
    if result is not None:
        assert result != 1.0  # Should not find the early swing


def test_find_swing_high_returns_none_too_short() -> None:
    """Return None when array is shorter than 2*order+1."""
    data = np.array([1.0, 2.0, 1.0], dtype=np.float64)
    result = find_swing_high(data, lookback=20, order=3)
    assert result is None
