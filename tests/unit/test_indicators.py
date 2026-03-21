"""Tests for technical indicators."""

import numpy as np
import pytest

from libs.indicators.moving_averages import ema, sma, vwma
from libs.indicators.oscillators import rsi


class TestSMA:
    def test_basic_sma(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(values, 3)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)  # (1+2+3)/3
        assert result[3] == pytest.approx(3.0)  # (2+3+4)/3
        assert result[4] == pytest.approx(4.0)  # (3+4+5)/3

    def test_sma_period_1(self) -> None:
        values = np.array([10.0, 20.0, 30.0])
        result = sma(values, 1)
        np.testing.assert_array_almost_equal(result, values)

    def test_sma_insufficient_data(self) -> None:
        values = np.array([1.0, 2.0])
        result = sma(values, 5)
        assert all(np.isnan(result))

    def test_sma_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            sma(np.array([1.0]), 0)


class TestEMA:
    def test_ema_first_value_is_sma(self) -> None:
        values = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        result = ema(values, 3)
        # First EMA value (index 2) should equal SMA of first 3 values
        assert result[2] == pytest.approx(4.0)

    def test_ema_converges_to_constant(self) -> None:
        values = np.full(50, 100.0)
        result = ema(values, 10)
        # EMA of a constant should be that constant
        assert result[-1] == pytest.approx(100.0)

    def test_ema_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            ema(np.array([1.0]), -1)


class TestVWMA:
    def test_vwma_equal_volumes(self) -> None:
        """With equal volumes, VWMA should equal SMA."""
        prices = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        volumes = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        result = vwma(prices, volumes, 3)
        sma_result = sma(prices, 3)
        # Where both are not NaN, they should be equal
        for i in range(len(result)):
            if not np.isnan(result[i]) and not np.isnan(sma_result[i]):
                assert result[i] == pytest.approx(sma_result[i])

    def test_vwma_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            vwma(np.array([1.0, 2.0]), np.array([1.0]), 1)


class TestRSI:
    def test_rsi_all_gains(self) -> None:
        """Monotonically increasing prices should give RSI near 100."""
        values = np.arange(1.0, 30.0)
        result = rsi(values, 14)
        assert result[-1] == pytest.approx(100.0)

    def test_rsi_all_losses(self) -> None:
        """Monotonically decreasing prices should give RSI near 0."""
        values = np.arange(30.0, 1.0, -1.0)
        result = rsi(values, 14)
        assert result[-1] == pytest.approx(0.0, abs=0.1)

    def test_rsi_range(self) -> None:
        """RSI should always be between 0 and 100."""
        np.random.seed(42)
        values = np.cumsum(np.random.randn(200)) + 100
        result = rsi(values, 14)
        valid = result[~np.isnan(result)]
        assert all(0 <= v <= 100 for v in valid)

    def test_rsi_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            rsi(np.array([1.0]), 0)
