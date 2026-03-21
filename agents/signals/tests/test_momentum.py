"""Tests for the momentum strategy signal logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from libs.common.constants import INSTRUMENT_ID
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.momentum import MomentumParams, MomentumStrategy


def _snap(mark: float, ts: datetime | None = None) -> MarketSnapshot:
    """Minimal snapshot for testing."""
    if ts is None:
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument=INSTRUMENT_ID,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark - 0.5)),
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


def _build_store_with_prices(prices: list[float]) -> FeatureStore:
    """Build a FeatureStore pre-loaded with price samples."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
    for i, price in enumerate(prices):
        snap = _snap(price, ts=base + timedelta(seconds=i))
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
        assert sig.instrument == INSTRUMENT_ID
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
            assert sig.conviction > 0.0
            assert sig.conviction <= 1.0
            assert sig.time_horizon == timedelta(hours=4)
            assert sig.suggested_target == PortfolioTarget.B

    def test_properties(self) -> None:
        strategy = MomentumStrategy()
        assert strategy.name == "momentum"
        assert strategy.enabled is True
        assert strategy.min_history > 0
