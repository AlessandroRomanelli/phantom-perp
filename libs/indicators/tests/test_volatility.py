"""Tests for volatility indicators — Bollinger Bands contract verification."""

from __future__ import annotations

import numpy as np
import pytest

from libs.indicators.volatility import bollinger_bands


def test_bollinger_bands_uses_sample_std() -> None:
    """Bollinger Bands must use ddof=1 (sample std).

    Window [1,2,3,4,5]:
      ddof=1: std = sqrt(2.5) ≈ 1.5811, upper = 3.0 + 2*1.5811 = 6.1623
      ddof=0: std = sqrt(2.0) ≈ 1.4142, upper = 3.0 + 2*1.4142 = 5.8284
    """
    values = np.arange(1.0, 11.0, dtype=np.float64)
    result = bollinger_bands(values, period=5, num_std=2.0)

    expected_std_ddof1 = np.std([1.0, 2.0, 3.0, 4.0, 5.0], ddof=1)  # ≈ 1.5811
    expected_upper = 3.0 + 2.0 * expected_std_ddof1  # ≈ 6.1623

    assert abs(result.upper[4] - expected_upper) < 0.001, (
        f"Expected upper ≈ {expected_upper:.4f} (ddof=1), got {result.upper[4]:.4f}. "
        "Check that bollinger_bands uses ddof=1."
    )


def test_bollinger_constant_values_zero_bandwidth() -> None:
    """Bollinger Bands with constant input must have zero bandwidth (std=0 regardless of ddof)."""
    values = np.full(10, 5.0, dtype=np.float64)
    result = bollinger_bands(values, period=5, num_std=2.0)

    tail_bandwidth = result.bandwidth[4:]
    assert np.all(tail_bandwidth == 0.0), (
        f"Expected all-zero bandwidth for constant input, got: {tail_bandwidth}"
    )
