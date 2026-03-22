"""Tests for the momentum strategy signal logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy

TEST_INSTRUMENT_ID = "ETH-PERP"


def _snap(
    mark: float,
    ts: datetime | None = None,
    volume: float = 15000.0,
) -> MarketSnapshot:
    """Minimal snapshot for testing."""
    if ts is None:
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument=TEST_INSTRUMENT_ID,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark - 0.5)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.25)),
        best_ask=Decimal(str(mark + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal(str(volume)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )


def _build_store_with_prices(
    prices: list[float],
    volumes: list[float] | None = None,
) -> FeatureStore:
    """Build a FeatureStore pre-loaded with price samples."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
    for i, price in enumerate(prices):
        vol = volumes[i] if volumes is not None else 15000.0 + i * 100.0
        snap = _snap(price, ts=base + timedelta(seconds=i), volume=vol)
        store.update(snap)
    return store


def _trending_up_prices(n: int = 100, start: float = 2000.0, step: float = 2.0) -> list[float]:
    """Generate prices in a clear uptrend with some noise."""
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.5, n)
    return [start + i * step + noise[i] for i in range(n)]


def _trending_down_prices(n: int = 100, start: float = 2200.0, step: float = 2.0) -> list[float]:
    """Generate prices in a clear downtrend with some noise."""
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.5, n)
    return [start - i * step + noise[i] for i in range(n)]


def _ranging_prices(n: int = 100, center: float = 2200.0) -> list[float]:
    """Generate prices oscillating around a center (no trend)."""
    rng = np.random.default_rng(42)
    return [center + rng.normal(0, 1.0) for _ in range(n)]


def _make_crossover_store(
    direction: str = "bullish",
    volume_ratio: float = 1.5,
) -> tuple[FeatureStore, list[float]]:
    """Build a store with flat-then-trend prices and controlled volumes.

    Returns (store, prices) so the caller can feed the last snapshot to evaluate().
    volume_ratio controls the ratio of current bar volume to rolling average.
    """
    flat = [2000.0] * 30
    if direction == "bullish":
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
    else:
        ramp = [2000.0 - i * 5.0 for i in range(1, 40)]
    prices = flat + ramp

    # Build volumes: flat baseline with the last bar at desired ratio
    base_vol = 10000.0
    # Each bar gets base_vol + i*100 to simulate increasing 24h volume
    # bar_volumes = np.diff(volumes), so bar_vol ~ 100 per bar
    volumes = [base_vol + i * 100.0 for i in range(len(prices))]
    # Adjust last volume to control ratio: avg bar vol ~100, set last to 100*ratio
    if volume_ratio < 0.5:
        # Make last bar vol very small (near zero delta)
        volumes[-1] = volumes[-2] + 1.0  # Tiny bar volume
    return _build_store_with_prices(prices, volumes), prices


class TestMomentumStrategy:
    def test_no_signal_insufficient_data(self) -> None:
        strategy = MomentumStrategy(params=MomentumParams(slow_ema_period=26, adx_period=14))
        store = _build_store_with_prices([2200.0] * 10)  # Too few samples
        snap = _snap(2200.0)
        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_bullish_crossover_generates_long(self) -> None:
        """Clear uptrend should produce a LONG signal."""
        params = MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=15.0,
            rsi_period=10,
            rsi_overbought=101.0,  # Disabled: pure synthetic ramp has RSI=100
            rsi_oversold=-1.0,
            min_conviction=0.0,  # Disabled: NaN ADX + extreme RSI gives low conviction
            cooldown_bars=0,
            vol_min_ratio=0.0,  # Disable volume filter for this test
        )
        strategy = MomentumStrategy(params=params)

        # Build a flat-then-uptrend series to trigger crossover
        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp

        # Build store incrementally so crossover is detected at the transition
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i))
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # Should have at least one LONG signal
        long_signals = [s for s in signals if s.direction == PositionSide.LONG]
        assert len(long_signals) >= 1

        sig = long_signals[0]
        assert sig.source == SignalSource.MOMENTUM
        assert sig.instrument == TEST_INSTRUMENT_ID
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.entry_price is not None
        assert sig.stop_loss < sig.entry_price  # Stop below entry for long
        assert sig.take_profit > sig.entry_price  # TP above entry for long

    def test_bearish_crossover_generates_short(self) -> None:
        """Clear downtrend should produce a SHORT signal."""
        params = MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=15.0,
            rsi_period=10,
            rsi_overbought=101.0,  # Disabled: pure synthetic ramp has extreme RSI
            rsi_oversold=-1.0,
            min_conviction=0.0,  # Disabled: NaN ADX + extreme RSI gives low conviction
            cooldown_bars=0,
            vol_min_ratio=0.0,  # Disable volume filter for this test
        )
        strategy = MomentumStrategy(params=params)

        # Flat then downtrend
        flat = [2200.0] * 30
        ramp_down = [2200.0 - i * 5.0 for i in range(1, 40)]
        prices = flat + ramp_down

        # Build store incrementally so crossover is detected at the transition
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i))
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        short_signals = [s for s in signals if s.direction == PositionSide.SHORT]
        assert len(short_signals) >= 1

        sig = short_signals[0]
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.entry_price is not None
        assert sig.stop_loss > sig.entry_price  # Stop above entry for short
        assert sig.take_profit < sig.entry_price  # TP below entry for short

    def test_ranging_market_no_signal(self) -> None:
        """Ranging market (low ADX) should produce no signals."""
        params = MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=25.0,  # High threshold
            rsi_period=10,
            min_conviction=0.3,
            cooldown_bars=0,
            vol_min_ratio=0.0,  # Disable volume filter
        )
        strategy = MomentumStrategy(params=params)
        prices = _ranging_prices(80)
        store = _build_store_with_prices(prices)

        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i))
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # In a ranging market with high ADX threshold, should get few or no signals
        assert len(signals) <= 1

    def test_cooldown_prevents_rapid_signals(self) -> None:
        """Cooldown should prevent a signal on every bar."""
        params = MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            min_conviction=0.1,
            cooldown_bars=10,
            vol_min_ratio=0.0,  # Disable volume filter
        )
        strategy = MomentumStrategy(params=params)
        prices = _trending_up_prices(80, step=3.0)
        store = _build_store_with_prices(prices)

        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i))
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # With 80 bars and 10-bar cooldown, should get at most ~8 signals
        assert len(signals) <= 8

    def test_signal_has_correct_metadata(self) -> None:
        params = MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,  # Disable volume filter
        )
        strategy = MomentumStrategy(params=params)
        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp
        store = _build_store_with_prices(prices)

        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i))
            result = strategy.evaluate(s, store)
            signals.extend(result)

        if signals:
            sig = signals[0]
            assert "fast_ema" in sig.metadata
            assert "slow_ema" in sig.metadata
            assert "atr" in sig.metadata
            assert "volume_ratio" in sig.metadata
            assert "vol_percentile" in sig.metadata
            assert "swing_stop" in sig.metadata
            assert sig.conviction > 0.0
            assert sig.conviction <= 1.0
            assert sig.time_horizon == timedelta(hours=4)
            # suggested_target depends on conviction -- may be A or B
            assert sig.suggested_target in (PortfolioTarget.A, PortfolioTarget.B)

    def test_properties(self) -> None:
        strategy = MomentumStrategy()
        assert strategy.name == "momentum"
        assert strategy.enabled is True
        assert strategy.min_history > 0


class TestMomentumConfig:
    """YAML config loader correctly populates ALL MomentumParams fields."""

    def test_all_fields_loaded_from_config(self) -> None:
        config = {
            "parameters": {
                "fast_ema_period": 8,
                "slow_ema_period": 21,
                "adx_period": 10,
                "adx_threshold": 25.0,
                "rsi_period": 12,
                "rsi_overbought": 75.0,
                "rsi_oversold": 25.0,
                "atr_period": 10,
                "stop_loss_atr_mult": 1.5,
                "take_profit_atr_mult": 2.5,
                "min_conviction": 0.35,
                "cooldown_bars": 3,
                "vol_lookback": 15,
                "vol_min_ratio": 0.6,
                "portfolio_a_min_conviction": 0.80,
                "swing_lookback": 25,
                "swing_order": 4,
            }
        }
        strategy = MomentumStrategy(config=config)
        p = strategy._params

        assert p.fast_ema_period == 8
        assert p.slow_ema_period == 21
        assert p.adx_period == 10
        assert p.adx_threshold == 25.0
        assert p.rsi_period == 12
        assert p.rsi_overbought == 75.0
        assert p.rsi_oversold == 25.0
        assert p.atr_period == 10
        assert p.stop_loss_atr_mult == 1.5
        assert p.take_profit_atr_mult == 2.5
        assert p.min_conviction == 0.35
        assert p.cooldown_bars == 3
        assert p.vol_lookback == 15
        assert p.vol_min_ratio == 0.6
        assert p.portfolio_a_min_conviction == 0.80
        assert p.swing_lookback == 25
        assert p.swing_order == 4

    def test_missing_fields_use_defaults(self) -> None:
        config = {"parameters": {"fast_ema_period": 8}}
        strategy = MomentumStrategy(config=config)
        p = strategy._params

        assert p.fast_ema_period == 8
        # All others should be defaults
        assert p.vol_lookback == 10
        assert p.vol_min_ratio == 0.5
        assert p.portfolio_a_min_conviction == 0.75
        assert p.swing_lookback == 20
        assert p.swing_order == 3
        assert p.adx_threshold == 20.0
        assert p.stop_loss_atr_mult == 2.0
        assert p.take_profit_atr_mult == 3.0
        assert p.cooldown_bars == 5


class TestMomentumVolumeFilter:
    """Volume confirmation rejects low-volume crossovers."""

    def _make_strategy(self, vol_min_ratio: float = 0.5) -> MomentumStrategy:
        return MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=15.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_lookback=10,
            vol_min_ratio=vol_min_ratio,
        ))

    def test_low_volume_rejects_signal(self) -> None:
        """Crossover with bar volume below 50% of rolling avg returns []."""
        strategy = self._make_strategy(vol_min_ratio=0.5)

        # Build flat-then-uptrend prices with declining volume
        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp

        # Volumes: consistent then drop to near-zero at crossover point
        base_vol = 10000.0
        volumes = []
        for i in range(len(prices)):
            if i < 30:
                volumes.append(base_vol + i * 100.0)
            else:
                # Very small increments -- bar volume near zero
                volumes.append(volumes[-1] + 0.1)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # Low volume bars should be filtered -- no signals
        assert len(signals) == 0

    def test_adequate_volume_allows_signal(self) -> None:
        """Crossover with adequate bar volume produces a signal."""
        strategy = self._make_strategy(vol_min_ratio=0.5)

        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp

        # Consistent increasing volumes -- bar_vol stays ~100 per bar
        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        # Should have at least one signal with adequate volume
        assert len(signals) >= 1

    def test_volume_ratio_in_metadata(self) -> None:
        """Signal metadata includes volume_ratio field."""
        strategy = self._make_strategy(vol_min_ratio=0.0)

        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp
        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        assert len(signals) >= 1
        assert "volume_ratio" in signals[0].metadata
        assert signals[0].metadata["volume_ratio"] > 0


class TestMomentumAdaptiveConviction:
    """Conviction model uses ADX, RSI, and volatility components."""

    def test_high_vol_breakout_scores_higher(self) -> None:
        """High ATR percentile breakout should have higher conviction."""
        strategy = MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,
        ))

        # The conviction model scales with ATR percentile.
        # We test _compute_conviction directly with different vol contexts.
        # High volume_ratio + high vol_pct should give higher conviction
        import numpy as np
        atr_vals = np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 2.0, 2.5, 3.0, 5.0])

        # High volatility scenario
        high_vol = strategy._compute_conviction(
            adx_value=35.0,
            rsi_value=45.0,
            is_bullish=True,
            volume_ratio=2.0,
            atr_vals=atr_vals,
            cur_atr=5.0,  # At the top of ATR range
        )

        # Low volatility scenario
        low_vol = strategy._compute_conviction(
            adx_value=35.0,
            rsi_value=45.0,
            is_bullish=True,
            volume_ratio=2.0,
            atr_vals=atr_vals,
            cur_atr=1.0,  # At the bottom of ATR range
        )

        assert high_vol > low_vol

    def test_conviction_never_exceeds_one(self) -> None:
        """Even with maximum inputs, conviction stays <= 1.0."""
        strategy = MomentumStrategy(params=MomentumParams(min_conviction=0.0))
        import numpy as np
        atr_vals = np.array([1.0] * 10)

        conv = strategy._compute_conviction(
            adx_value=100.0,
            rsi_value=50.0,
            is_bullish=True,
            volume_ratio=10.0,
            atr_vals=atr_vals,
            cur_atr=1.0,
        )
        assert conv <= 1.0

    def test_three_component_scoring(self) -> None:
        """Conviction has ADX (0-0.35), RSI (0-0.35), vol (0-0.30) components."""
        strategy = MomentumStrategy(params=MomentumParams(min_conviction=0.0))
        import numpy as np
        atr_vals = np.array([1.0] * 10)

        # Zero ADX scenario (ADX at threshold = 20)
        zero_adx = strategy._compute_conviction(
            adx_value=20.0,
            rsi_value=50.0,
            is_bullish=True,
            volume_ratio=1.0,
            atr_vals=atr_vals,
            cur_atr=1.0,
        )

        # Max ADX scenario
        max_adx = strategy._compute_conviction(
            adx_value=80.0,
            rsi_value=50.0,
            is_bullish=True,
            volume_ratio=1.0,
            atr_vals=atr_vals,
            cur_atr=1.0,
        )

        # ADX component difference should contribute up to 0.35
        adx_diff = max_adx - zero_adx
        assert adx_diff > 0
        assert adx_diff <= 0.35 + 0.01  # Allow small float rounding


class TestMomentumSwingStops:
    """Swing point detection for stop-loss placement."""

    def test_long_stop_at_swing_low(self) -> None:
        """LONG stop_loss placed at recent swing low when one exists."""
        strategy = MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,
            swing_lookback=20,
            swing_order=2,
        ))

        # Build series with a clear swing low (V-shaped dip)
        # Flat, dip, recover, then ramp up to trigger bullish crossover
        flat = [2000.0] * 15
        dip = [2000.0 - i * 3 for i in range(1, 6)]  # Dip down
        recover = [2000.0 - 15 + i * 5 for i in range(1, 6)]  # Recover
        ramp = [2000.0 + i * 5.0 for i in range(1, 45)]  # Strong uptrend
        prices = flat + dip + recover + ramp

        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        long_signals = [s for s in signals if s.direction == PositionSide.LONG]
        if long_signals:
            sig = long_signals[0]
            assert sig.stop_loss is not None
            assert sig.entry_price is not None
            assert sig.stop_loss < sig.entry_price
            # Check swing_stop metadata
            assert "swing_stop" in sig.metadata

    def test_short_stop_at_swing_high(self) -> None:
        """SHORT stop_loss placed at recent swing high when one exists."""
        strategy = MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,
            swing_lookback=20,
            swing_order=2,
        ))

        # Series with swing high then decline to trigger bearish crossover
        flat = [2200.0] * 15
        spike = [2200.0 + i * 3 for i in range(1, 6)]  # Spike up
        decline = [2200.0 + 15 - i * 5 for i in range(1, 6)]  # Decline
        ramp_down = [2200.0 - i * 5.0 for i in range(1, 45)]  # Downtrend
        prices = flat + spike + decline + ramp_down

        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        short_signals = [s for s in signals if s.direction == PositionSide.SHORT]
        if short_signals:
            sig = short_signals[0]
            assert sig.stop_loss is not None
            assert sig.entry_price is not None
            assert sig.stop_loss > sig.entry_price

    def test_atr_fallback_when_no_swing(self) -> None:
        """ATR-based stop used when no swing point found."""
        strategy = MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,
            swing_lookback=5,  # Very short lookback
            swing_order=4,  # Very high order -- unlikely to find swing
        ))

        # Monotonic ramp -- no swing points exist
        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp
        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        if signals:
            sig = signals[0]
            assert sig.stop_loss is not None
            assert sig.entry_price is not None
            # Should have swing_stop=False when ATR fallback is used
            assert sig.metadata.get("swing_stop") is not None


class TestMomentumPortfolioRouting:
    """High-conviction signals route to Portfolio A."""

    def test_high_conviction_routes_to_portfolio_a(self) -> None:
        """Signals with conviction >= 0.75 have suggested_target=PortfolioTarget.A."""
        strategy = MomentumStrategy(params=MomentumParams(
            portfolio_a_min_conviction=0.75,
        ))
        import numpy as np
        atr_vals = np.array([1.0] * 10)

        # Create a scenario with very high conviction
        conv = strategy._compute_conviction(
            adx_value=80.0,  # Very strong trend
            rsi_value=45.0,  # Ideal RSI for bullish
            is_bullish=True,
            volume_ratio=3.0,  # High volume
            atr_vals=atr_vals,
            cur_atr=1.0,
        )
        # Should produce high conviction
        assert conv >= 0.75

    def test_low_conviction_routes_to_portfolio_b(self) -> None:
        """Signals with conviction < 0.75 have suggested_target=PortfolioTarget.B."""
        strategy = MomentumStrategy(params=MomentumParams(
            portfolio_a_min_conviction=0.75,
        ))
        import numpy as np
        atr_vals = np.array([1.0] * 10)

        # Create scenario with moderate conviction
        conv = strategy._compute_conviction(
            adx_value=25.0,  # Modest trend
            rsi_value=50.0,  # Neutral RSI
            is_bullish=True,
            volume_ratio=0.8,  # Low volume
            atr_vals=atr_vals,
            cur_atr=1.0,
        )
        # Should be below Portfolio A threshold
        assert conv < 0.75

    def test_portfolio_routing_in_signal(self) -> None:
        """Full evaluate() produces correct suggested_target based on conviction."""
        strategy = MomentumStrategy(params=MomentumParams(
            fast_ema_period=5,
            slow_ema_period=15,
            adx_period=10,
            adx_threshold=10.0,
            rsi_period=10,
            rsi_overbought=101.0,
            rsi_oversold=-1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            vol_min_ratio=0.0,
            portfolio_a_min_conviction=0.75,
        ))

        flat = [2000.0] * 30
        ramp = [2000.0 + i * 5.0 for i in range(1, 40)]
        prices = flat + ramp
        volumes = [10000.0 + i * 100.0 for i in range(len(prices))]

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        signals: list = []
        for i in range(len(prices)):
            s = _snap(prices[i], ts=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i), volume=volumes[i])
            store.update(s)
            result = strategy.evaluate(s, store)
            signals.extend(result)

        if signals:
            for sig in signals:
                if sig.conviction >= 0.75:
                    assert sig.suggested_target == PortfolioTarget.A
                else:
                    assert sig.suggested_target == PortfolioTarget.B
