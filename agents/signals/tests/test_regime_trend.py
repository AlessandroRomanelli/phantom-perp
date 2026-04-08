"""Tests for the regime-filtered trend following strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.regime_trend import RegimeTrendParams, RegimeTrendStrategy

TEST_INSTRUMENT_ID = "ETH-PERP"


def _snap(
    mark: float,
    ts: datetime | None = None,
    index: float | None = None,
) -> MarketSnapshot:
    """Minimal snapshot for testing."""
    if ts is None:
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    idx = index if index is not None else mark - 0.5
    return MarketSnapshot(
        timestamp=ts,
        instrument=TEST_INSTRUMENT_ID,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(idx)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.25)),
        best_ask=Decimal(str(mark + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal("15000"),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )


def _build_store(prices: list[float], index_prices: list[float] | None = None) -> FeatureStore:
    """Build a FeatureStore pre-loaded with price/index samples."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
    for i, price in enumerate(prices):
        idx = index_prices[i] if index_prices else price - 0.5
        snap = _snap(price, ts=base + timedelta(seconds=i), index=idx)
        store.update(snap)
    return store


def _strong_uptrend(
    n: int = 120,
    start: float = 2000.0,
    step: float = 3.0,
) -> tuple[list[float], list[float]]:
    """Generate a strong uptrend with expanding volatility and spot confirmation.

    Returns (perp_prices, index_prices) — spot trends in sync with perp.
    """
    rng = np.random.default_rng(42)
    # Start flat to establish baseline ATR, then trend up strongly
    flat_n = 60
    trend_n = n - flat_n
    flat = [start + rng.normal(0, 1.0) for _ in range(flat_n)]
    # Trend phase: increasingly volatile to ensure ATR expansion
    trend = []
    for i in range(trend_n):
        noise = rng.normal(0, 2.0 + i * 0.05)
        trend.append(start + (i + 1) * step + noise)
    prices = flat + trend
    # Spot tracks perp closely (confirming)
    index_prices = [p - 0.5 + rng.normal(0, 0.3) for p in prices]
    return prices, index_prices


def _strong_downtrend(
    n: int = 120,
    start: float = 2200.0,
    step: float = 3.0,
) -> tuple[list[float], list[float]]:
    """Generate a strong downtrend with expanding volatility and spot confirmation."""
    rng = np.random.default_rng(42)
    flat_n = 60
    trend_n = n - flat_n
    flat = [start + rng.normal(0, 1.0) for _ in range(flat_n)]
    trend = []
    for i in range(trend_n):
        noise = rng.normal(0, 2.0 + i * 0.05)
        trend.append(start - (i + 1) * step + noise)
    prices = flat + trend
    index_prices = [p - 0.5 + rng.normal(0, 0.3) for p in prices]
    return prices, index_prices


def _ranging_prices(n: int = 120, center: float = 2200.0) -> tuple[list[float], list[float]]:
    """Ranging market — no trend, low vol."""
    rng = np.random.default_rng(42)
    prices = [center + rng.normal(0, 1.5) for _ in range(n)]
    index_prices = [p - 0.5 + rng.normal(0, 0.3) for p in prices]
    return prices, index_prices


def _relaxed_params() -> RegimeTrendParams:
    """Params with relaxed thresholds for testing entry detection."""
    return RegimeTrendParams(
        trend_ema_period=20,
        trend_slope_lookback=3,
        adx_period=10,
        adx_threshold=10.0,
        atr_period=10,
        atr_avg_period=15,
        atr_expansion_threshold=0.8,
        spot_ema_period=10,
        spot_slope_lookback=3,
        fast_ema_period=10,
        breakout_lookback=10,
        pullback_tolerance_atr=0.5,
        stop_loss_atr_mult=2.5,
        take_profit_atr_mult=4.0,
        min_conviction=0.0,
        cooldown_bars=0,
    )


class TestRegimeTrendStrategy:
    def test_properties(self) -> None:
        strategy = RegimeTrendStrategy()
        assert strategy.name == "regime_trend"
        assert strategy.enabled is True
        assert strategy.min_history > 0

    def test_no_signal_insufficient_data(self) -> None:
        strategy = RegimeTrendStrategy()
        store = _build_store([2200.0] * 10)
        snap = _snap(2200.0)
        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_ranging_market_no_signal(self) -> None:
        """Ranging market should fail the trend and vol filters."""
        params = _relaxed_params()
        params.adx_threshold = 30.0  # Require strong trend
        params.atr_expansion_threshold = 1.3  # Require vol expansion
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _ranging_prices(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) == 0

    def test_uptrend_generates_long(self) -> None:
        """Strong uptrend with all filters aligning should produce LONG."""
        params = _relaxed_params()
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        long_signals = [s for s in signals if s.direction == PositionSide.LONG]
        assert len(long_signals) >= 1

        sig = long_signals[0]
        assert sig.source == SignalSource.REGIME_TREND
        assert sig.instrument == TEST_INSTRUMENT_ID
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.entry_price is not None
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit > sig.entry_price

    def test_downtrend_generates_short(self) -> None:
        """Strong downtrend with all filters aligning should produce SHORT."""
        params = _relaxed_params()
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_downtrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        short_signals = [s for s in signals if s.direction == PositionSide.SHORT]
        assert len(short_signals) >= 1

        sig = short_signals[0]
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.entry_price is not None
        assert sig.stop_loss > sig.entry_price
        assert sig.take_profit < sig.entry_price

    def test_spot_divergence_blocks_long(self) -> None:
        """Perp uptrend with spot trending down should block LONG signals."""
        params = _relaxed_params()
        strategy = RegimeTrendStrategy(params=params)
        prices, _ = _strong_uptrend(120)
        # Spot trends down while perp trends up — diverges during trend phase
        rng = np.random.default_rng(99)
        divergent_index = [2000.0 - i * 0.5 + rng.normal(0, 0.3) for i in range(120)]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=divergent_index[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # No LONG signals should fire — spot doesn't confirm the uptrend
        long_signals = [s for s in signals if s.direction == PositionSide.LONG]
        assert len(long_signals) == 0

    def test_low_vol_blocks_signal(self) -> None:
        """Uptrend with contracting vol should be filtered out."""
        params = _relaxed_params()
        params.atr_expansion_threshold = 1.5  # Require strong expansion
        strategy = RegimeTrendStrategy(params=params)

        # Mild trend with decreasing volatility
        rng = np.random.default_rng(42)
        prices = []
        for i in range(120):
            # Noise decreases over time (vol contracts)
            noise = rng.normal(0, max(0.1, 3.0 - i * 0.025))
            prices.append(2000.0 + i * 1.0 + noise)
        index_prices = [p - 0.5 for p in prices]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) == 0

    def test_cooldown_prevents_rapid_signals(self) -> None:
        """Cooldown should throttle signal frequency."""
        params = _relaxed_params()
        params.cooldown_bars = 15
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # With 15-bar cooldown and ~60 trending bars, evaluate() fires at most ~4-6
        # times, but each can emit up to 2 signals (A + B), so bound is ~12
        b_signals = [s for s in signals if s.suggested_route == Route.B]
        assert len(b_signals) <= 8

    def test_signal_metadata(self) -> None:
        """Emitted signals should carry full filter metadata."""
        params = _relaxed_params()
        params.route_a_enabled = False  # Only check B signal metadata
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) >= 1
        sig = signals[0]
        assert "entry_type" in sig.metadata
        assert sig.metadata["entry_type"] in ("breakout", "pullback")
        assert "trend_ema" in sig.metadata
        assert "trend_slope" in sig.metadata
        assert "atr" in sig.metadata
        assert "atr_ratio" in sig.metadata
        assert "spot_ema" in sig.metadata
        assert "spot_slope" in sig.metadata
        assert sig.conviction > 0.0
        assert sig.conviction <= 1.0
        assert sig.time_horizon == timedelta(hours=6)
        assert sig.suggested_route == Route.B

    def test_route_a_routing_on_high_conviction_breakout(self) -> None:
        """High-conviction breakouts should emit a Portfolio A signal too."""
        params = _relaxed_params()
        params.route_a_enabled = True
        params.route_a_min_conviction = 0.3
        params.route_a_breakout_only = True
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        a_signals = [s for s in signals if s.suggested_route == Route.A]
        b_signals = [s for s in signals if s.suggested_route == Route.B]

        # Should have both A and B signals
        assert len(a_signals) >= 1
        assert len(b_signals) >= 1

        # A signals should have shorter horizon and tighter stops
        a_sig = a_signals[0]
        b_sig = b_signals[0]
        assert a_sig.time_horizon == timedelta(hours=2)
        assert b_sig.time_horizon == timedelta(hours=6)
        assert a_sig.metadata["route"] == "A"
        assert b_sig.metadata["route"] == "B"
        assert a_sig.metadata["entry_type"] == "breakout"

    def test_route_a_not_emitted_when_disabled(self) -> None:
        """When route_a_enabled is False, only B signals should emit."""
        params = _relaxed_params()
        params.route_a_enabled = False
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        a_signals = [s for s in signals if s.suggested_route == Route.A]
        assert len(a_signals) == 0

    def test_route_a_requires_min_conviction(self) -> None:
        """Portfolio A signal should not emit if conviction is below threshold."""
        params = _relaxed_params()
        params.route_a_enabled = True
        params.route_a_min_conviction = 0.99  # Almost impossible to hit
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        a_signals = [s for s in signals if s.suggested_route == Route.A]
        b_signals = [s for s in signals if s.suggested_route == Route.B]
        assert len(a_signals) == 0
        assert len(b_signals) >= 1  # B should still fire

    def test_config_override(self) -> None:
        """YAML config dict should override default params."""
        config = {
            "parameters": {
                "trend_ema_period": 40,
                "adx_threshold": 18.0,
                "min_conviction": 0.3,
                "route_a_min_conviction": 0.8,
            }
        }
        strategy = RegimeTrendStrategy(config=config)
        assert strategy._params.trend_ema_period == 40
        assert strategy._params.adx_threshold == 18.0
        assert strategy._params.min_conviction == 0.3
        assert strategy._params.route_a_min_conviction == 0.8
        # Unset params keep defaults
        assert strategy._params.atr_period == 14
        assert strategy._params.route_a_enabled is True


class TestAdaptiveThresholds:
    """Tests for RT-01: adaptive ADX/ATR expansion thresholds."""

    def test_low_vol_reduces_adx_threshold(self) -> None:
        """In low volatility (~20th percentile), ADX threshold is reduced."""
        # Create ATR history where current ATR is at ~20th percentile
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])  # 10.0 to 34.5
        cur_atr = 14.0  # Near the low end (~20th percentile)
        p = RegimeTrendParams(adx_threshold=22.0)

        adaptive_adx, _ = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        # Low vol -> multiplier ~0.8 -> threshold ~17.6 (lower than 22.0)
        assert adaptive_adx < p.adx_threshold

    def test_high_vol_increases_adx_threshold(self) -> None:
        """In high volatility (~80th percentile), ADX threshold is increased."""
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])  # 10.0 to 34.5
        cur_atr = 30.0  # Near the high end (~80th percentile)
        p = RegimeTrendParams(adx_threshold=22.0)

        adaptive_adx, _ = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        # High vol -> multiplier ~1.2 -> threshold ~26.4 (higher than 22.0)
        assert adaptive_adx > p.adx_threshold

    def test_low_vol_reduces_atr_expansion_threshold(self) -> None:
        """In low volatility, ATR expansion threshold is reduced."""
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])
        cur_atr = 14.0  # Low percentile
        p = RegimeTrendParams(atr_expansion_threshold=1.1)

        _, adaptive_atr_exp = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        # Low vol -> multiplier ~0.85 -> threshold ~0.935 (lower than 1.1)
        assert adaptive_atr_exp < p.atr_expansion_threshold

    def test_high_vol_increases_atr_expansion_threshold(self) -> None:
        """In high volatility, ATR expansion threshold is increased."""
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])
        cur_atr = 30.0  # High percentile
        p = RegimeTrendParams(atr_expansion_threshold=1.1)

        _, adaptive_atr_exp = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        # High vol -> multiplier ~1.15 -> threshold ~1.265 (higher than 1.1)
        assert adaptive_atr_exp > p.atr_expansion_threshold

    def test_adx_threshold_clamped_min(self) -> None:
        """Adaptive ADX threshold is clamped at minimum 15.0."""
        # Very low volatility percentile with a low base threshold
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])
        cur_atr = 10.0  # At 0th percentile
        p = RegimeTrendParams(
            adx_threshold=18.0,
            adx_adapt_low_mult=0.8,
            adx_adapt_min=15.0,
            adx_adapt_max=35.0,
        )

        adaptive_adx, _ = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        assert adaptive_adx >= 15.0

    def test_adx_threshold_clamped_max(self) -> None:
        """Adaptive ADX threshold is clamped at maximum 35.0."""
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])
        cur_atr = 34.5  # At 100th percentile
        p = RegimeTrendParams(
            adx_threshold=30.0,  # High base
            adx_adapt_high_mult=1.2,
            adx_adapt_min=15.0,
            adx_adapt_max=35.0,
        )

        adaptive_adx, _ = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        assert adaptive_adx <= 35.0

    def test_atr_expansion_clamped_between_bounds(self) -> None:
        """Adaptive ATR expansion threshold is clamped between 0.8 and 1.5."""
        # Test min clamp: very low vol, low base threshold
        atr_vals = np.array([10.0 + i * 0.5 for i in range(50)])
        cur_atr = 10.0
        p = RegimeTrendParams(
            atr_expansion_threshold=0.9,
            atr_expand_adapt_low_mult=0.85,
            atr_expand_adapt_min=0.8,
            atr_expand_adapt_max=1.5,
        )

        _, adaptive_atr_exp = RegimeTrendStrategy._compute_adaptive_thresholds(
            atr_vals, cur_atr, p,
        )
        assert adaptive_atr_exp >= 0.8
        assert adaptive_atr_exp <= 1.5

    def test_adaptive_disabled_preserves_thresholds(self) -> None:
        """When adx_adapt_enabled=False, thresholds should be unchanged."""
        params = _relaxed_params()
        params.adx_adapt_enabled = False
        params.adx_threshold = 22.0
        params.atr_expansion_threshold = 1.1
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # When adaptive is disabled, metadata should show original thresholds
        if signals:
            sig = signals[0]
            assert sig.metadata.get("adaptive_adx_threshold") == 22.0
            assert sig.metadata.get("adaptive_atr_expansion") == 1.1


class TestTrailingStopMetadata:
    """Tests for RT-02: trailing stop metadata in signals."""

    def test_signal_contains_trail_metadata_keys(self) -> None:
        """Signal metadata contains trail_enabled, trail_activation_pct, trail_distance_atr."""
        params = _relaxed_params()
        params.trail_enabled = True
        params.trail_activation_pct = 1.0
        params.trail_distance_atr = 1.5
        params.initial_stop_atr_mult = 1.8
        params.route_a_enabled = False
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) >= 1
        sig = signals[0]
        assert "trail_enabled" in sig.metadata
        assert sig.metadata["trail_enabled"] is True
        assert "trail_activation_pct" in sig.metadata
        assert sig.metadata["trail_activation_pct"] == 1.0
        assert "trail_distance_atr" in sig.metadata
        assert sig.metadata["trail_distance_atr"] == 1.5

    def test_tighter_initial_stop_when_trail_enabled(self) -> None:
        """Portfolio B stop_loss uses tighter initial_stop_atr_mult (1.8) when trail enabled."""
        params = _relaxed_params()
        params.trail_enabled = True
        params.initial_stop_atr_mult = 1.8
        params.stop_loss_atr_mult = 2.5
        params.route_a_enabled = False
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) >= 1
        sig = signals[0]

        # Compute absolute stop distance regardless of direction
        entry = sig.entry_price
        sl = sig.stop_loss
        assert entry is not None and sl is not None
        stop_dist = abs(float(entry - sl))

        # Now compare with a non-trail version
        params_no_trail = _relaxed_params()
        params_no_trail.trail_enabled = False
        params_no_trail.initial_stop_atr_mult = 1.8
        params_no_trail.stop_loss_atr_mult = 2.5
        params_no_trail.route_a_enabled = False
        strategy_no_trail = RegimeTrendStrategy(params=params_no_trail)

        store2 = FeatureStore(sample_interval=timedelta(seconds=0))
        signals2: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store2.update(s)
            result = strategy_no_trail.evaluate(s, store2)
            signals2.extend(result)

        assert len(signals2) >= 1
        sig2 = signals2[0]
        stop_dist2 = abs(float(sig2.entry_price - sig2.stop_loss))

        # Trail version should have tighter stop (smaller distance)
        assert stop_dist < stop_dist2

    def test_adaptive_threshold_values_in_metadata(self) -> None:
        """Adaptive threshold values appear in signal metadata."""
        params = _relaxed_params()
        params.adx_adapt_enabled = True
        params.route_a_enabled = False
        strategy = RegimeTrendStrategy(params=params)
        prices, index_prices = _strong_uptrend(120)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(len(prices)):
            s = _snap(prices[i], ts=base + timedelta(seconds=i), index=index_prices[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) >= 1
        sig = signals[0]
        assert "adaptive_adx_threshold" in sig.metadata
        assert "adaptive_atr_expansion" in sig.metadata
        assert isinstance(sig.metadata["adaptive_adx_threshold"], float)
        assert isinstance(sig.metadata["adaptive_atr_expansion"], float)


class TestRegimeTrendIndexPriceGuard:
    """RegimeTrendStrategy must return [] when index_price is the zero sentinel."""

    def test_returns_empty_when_index_price_zero(self) -> None:
        """When index_price is Decimal('0') (sentinel), no signals are emitted."""
        strategy = RegimeTrendStrategy()
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

        # Feed enough samples with zero index_price to exceed min_history
        for i in range(130):
            snap = _snap(2230.0 + i * 0.1, ts=base + timedelta(seconds=i), index=0.0)
            store.update(snap)

        result = strategy.evaluate(snap, store)
        assert result == [], (
            "RegimeTrendStrategy must return [] when index_price is the zero sentinel"
        )
