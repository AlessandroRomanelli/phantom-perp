"""Tests for adaptive conviction threshold scaling utility."""

from __future__ import annotations

import numpy as np

from agents.signals.adaptive_conviction import (
    AdaptiveConvictionResult,
    compute_adaptive_threshold,
)


def test_low_vol_returns_threshold_below_base() -> None:
    """Low-volatility environment (10th percentile ATR) should lower threshold."""
    # Create ATR values where cur_atr is at the low end
    atr_vals = np.linspace(0.5, 2.0, 50)
    cur_atr = 0.5  # Lowest in the range -> ~0th percentile
    result = compute_adaptive_threshold(atr_vals, cur_atr, base_threshold=0.50)

    assert isinstance(result, AdaptiveConvictionResult)
    assert result.adjusted_threshold < 0.50


def test_high_vol_returns_threshold_above_base() -> None:
    """High-volatility environment (90th percentile ATR) should raise threshold."""
    atr_vals = np.linspace(0.5, 2.0, 50)
    cur_atr = 1.9  # Near top of range -> ~93rd percentile
    result = compute_adaptive_threshold(atr_vals, cur_atr, base_threshold=0.50)

    assert result.adjusted_threshold > 0.50


def test_insufficient_samples_returns_base_unchanged() -> None:
    """With fewer than min_samples, return base threshold unchanged."""
    atr_vals = np.array([1.0, 1.5, 2.0])  # Only 3 samples
    result = compute_adaptive_threshold(
        atr_vals, cur_atr=1.5, base_threshold=0.50, min_samples=20
    )

    assert result.adjusted_threshold == 0.50
    assert result.volatility_percentile == 0.5


def test_volatility_percentile_in_valid_range() -> None:
    """Result volatility_percentile should be in [0, 1]."""
    atr_vals = np.linspace(0.5, 2.0, 50)
    for cur_atr in [0.5, 1.0, 1.5, 2.0]:
        result = compute_adaptive_threshold(atr_vals, cur_atr, base_threshold=0.50)
        assert 0.0 <= result.volatility_percentile <= 1.0


def test_nan_values_filtered() -> None:
    """NaN values in ATR array should be filtered before percentile calc."""
    atr_vals = np.array([np.nan] * 10 + list(np.linspace(0.5, 2.0, 30)))
    result = compute_adaptive_threshold(atr_vals, cur_atr=1.0, base_threshold=0.50, min_samples=20)

    assert isinstance(result, AdaptiveConvictionResult)
    assert 0.0 <= result.volatility_percentile <= 1.0


def test_frozen_dataclass() -> None:
    """AdaptiveConvictionResult should be immutable."""
    result = compute_adaptive_threshold(
        np.linspace(0.5, 2.0, 50), cur_atr=1.0, base_threshold=0.50
    )
    try:
        result.adjusted_threshold = 0.99  # type: ignore[misc]
        raise AssertionError("Should not allow mutation")
    except AttributeError:
        pass  # Expected for frozen dataclass
