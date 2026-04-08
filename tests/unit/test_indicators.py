"""Tests for technical indicators."""

import numpy as np
import pytest

from libs.indicators.funding import cumulative_funding, funding_rate_zscore
from libs.indicators.moving_averages import ema, sma, vwma
from libs.indicators.oscillators import adx, macd, rsi, stochastic
from libs.indicators.volatility import atr, bollinger_bands, realized_volatility
from libs.indicators.volume import obv, vwap


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


class TestATR:
    def test_known_value(self) -> None:
        """Constant H=110, L=100, C=105 for 20 bars -> ATR converges to 10."""
        n = 20
        highs = np.full(n, 110.0)
        lows = np.full(n, 100.0)
        closes = np.full(n, 105.0)
        result = atr(highs, lows, closes, period=14)
        assert result[-1] == pytest.approx(10.0, abs=0.5)

    def test_atr_always_positive(self) -> None:
        np.random.seed(42)
        base = np.cumsum(np.random.randn(100)) + 100
        highs = base + np.abs(np.random.randn(100))
        lows = base - np.abs(np.random.randn(100))
        closes = (highs + lows) / 2
        result = atr(highs, lows, closes, period=14)
        valid = result[~np.isnan(result)]
        assert all(v > 0 for v in valid)


class TestBollingerBands:
    def test_constant_input(self) -> None:
        values = np.full(30, 100.0)
        bb = bollinger_bands(values, period=20)
        # Where valid, upper == middle == lower == 100
        idx = 19  # first valid index
        assert bb.upper[idx] == pytest.approx(100.0)
        assert bb.middle[idx] == pytest.approx(100.0)
        assert bb.lower[idx] == pytest.approx(100.0)

    def test_upper_above_lower(self) -> None:
        np.random.seed(42)
        values = np.cumsum(np.random.randn(50)) + 100
        bb = bollinger_bands(values, period=20)
        for i in range(19, len(values)):
            assert bb.upper[i] >= bb.lower[i]


class TestRealizedVolatility:
    def test_constant_price_zero_vol(self) -> None:
        values = np.full(50, 100.0)
        result = realized_volatility(values, period=24)
        valid = result[~np.isnan(result)]
        assert all(v == pytest.approx(0.0) for v in valid)

    def test_positive_for_varying_prices(self) -> None:
        np.random.seed(42)
        values = np.cumsum(np.random.randn(100)) + 200
        values = np.abs(values)  # ensure positive
        result = realized_volatility(values, period=24)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert all(v > 0 for v in valid)


class TestOBV:
    def test_monotonic_increase(self) -> None:
        closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
        volumes = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        result = obv(closes, volumes)
        assert result[-1] == pytest.approx(400.0)

    def test_flat_prices_no_change(self) -> None:
        closes = np.array([10.0, 10.0, 10.0, 10.0, 10.0])
        volumes = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        result = obv(closes, volumes)
        assert result[-1] == pytest.approx(0.0)


class TestVWAPIndicator:
    def test_equal_prices_returns_price(self) -> None:
        n = 10
        highs = np.full(n, 100.0)
        lows = np.full(n, 100.0)
        closes = np.full(n, 100.0)
        volumes = np.full(n, 50.0)
        result = vwap(highs, lows, closes, volumes)
        assert result[-1] == pytest.approx(100.0)

    def test_hand_calculated(self) -> None:
        highs = np.array([12.0, 14.0])
        lows = np.array([10.0, 12.0])
        closes = np.array([11.0, 13.0])
        volumes = np.array([100.0, 200.0])
        result = vwap(highs, lows, closes, volumes)
        # TP1 = (12+10+11)/3 = 11.0, TP2 = (14+12+13)/3 = 13.0
        # VWAP[1] = (11*100 + 13*200) / (100+200) = 3700/300 = 12.333
        assert result[1] == pytest.approx(12.333, abs=0.01)


class TestMACD:
    def test_constant_input_zero_macd(self) -> None:
        values = np.full(50, 100.0)
        result = macd(values)
        valid = result.macd_line[~np.isnan(result.macd_line)]
        assert all(abs(v) < 1e-10 for v in valid)

    def test_returns_three_arrays(self) -> None:
        values = np.cumsum(np.random.randn(50)) + 100
        result = macd(values)
        assert len(result.macd_line) == 50
        assert len(result.signal_line) == 50
        assert len(result.histogram) == 50


class TestStochastic:
    def test_at_high_returns_100(self) -> None:
        n = 20
        highs = np.linspace(100, 120, n)
        lows = np.linspace(80, 100, n)
        closes = highs.copy()  # close at the high
        result = stochastic(highs, lows, closes, k_period=14)
        valid_k = result.k[~np.isnan(result.k)]
        assert valid_k[-1] == pytest.approx(100.0)

    def test_identical_hlc_returns_50(self) -> None:
        n = 20
        highs = np.full(n, 100.0)
        lows = np.full(n, 100.0)
        closes = np.full(n, 100.0)
        result = stochastic(highs, lows, closes, k_period=14)
        valid_k = result.k[~np.isnan(result.k)]
        assert all(v == pytest.approx(50.0) for v in valid_k)


class TestADX:
    def test_trending_market_high_adx(self) -> None:
        n = 100
        base = np.linspace(100, 200, n)
        highs = base + 1.0
        lows = base - 1.0
        closes = base.copy()
        result = adx(highs, lows, closes, period=14)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert valid[-1] > 25

    def test_ranging_market_low_adx(self) -> None:
        n = 200
        t = np.linspace(0, 20 * np.pi, n)
        base = 100 + 5 * np.sin(t)
        highs = base + 0.5
        lows = base - 0.5
        closes = base.copy()
        result = adx(highs, lows, closes, period=14)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert valid[-1] < 25


class TestFundingRateZScore:
    def test_constant_rates_zero_zscore(self) -> None:
        """Constant rates produce std~=0, so z-score is NaN or near-zero."""
        rates = np.full(200, 0.5)  # exact float avoids fp noise
        result = funding_rate_zscore(rates, lookback=168)
        # With zero std, all values remain NaN
        assert all(np.isnan(result))

    def test_spike_produces_high_zscore(self) -> None:
        rates = np.full(200, 0.01)
        rates[-1] = 0.10  # spike
        result = funding_rate_zscore(rates, lookback=168)
        # The last value should have a high z-score
        assert not np.isnan(result[-1])
        assert result[-1] > 2.0


class TestCumulativeFunding:
    def test_known_sum(self) -> None:
        rates = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
        result = cumulative_funding(rates, window_hours=3)
        assert result[2] == pytest.approx(0.06)  # 0.01+0.02+0.03
        assert result[4] == pytest.approx(0.12)  # 0.03+0.04+0.05


class TestIndicatorBoundary:
    def test_sma_empty(self) -> None:
        result = sma(np.array([]), 5)
        assert len(result) == 0

    def test_sma_single_element(self) -> None:
        result = sma(np.array([42.0]), 1)
        assert result[0] == pytest.approx(42.0)

    def test_ema_single_element(self) -> None:
        result = ema(np.array([42.0]), 1)
        assert result[0] == pytest.approx(42.0)

    def test_atr_single_bar(self) -> None:
        highs = np.array([110.0])
        lows = np.array([100.0])
        closes = np.array([105.0])
        result = atr(highs, lows, closes, period=1)
        assert result[0] == pytest.approx(10.0)

    def test_obv_single_bar(self) -> None:
        result = obv(np.array([50.0]), np.array([100.0]))
        assert result[0] == pytest.approx(0.0)

    def test_rsi_identical_values(self) -> None:
        values = np.full(30, 50.0)
        result = rsi(values, 14)
        valid = result[~np.isnan(result)]
        assert all(v == pytest.approx(100.0) for v in valid)

    def test_adx_insufficient_data(self) -> None:
        highs = np.array([110.0])
        lows = np.array([100.0])
        closes = np.array([105.0])
        result = adx(highs, lows, closes, period=14)
        assert all(np.isnan(result))
