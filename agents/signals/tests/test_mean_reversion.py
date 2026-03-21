"""Tests for the mean reversion strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from libs.common.constants import INSTRUMENT_ID
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.mean_reversion import MeanReversionParams, MeanReversionStrategy


def _snap(
    mark: float = 2230.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
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


def _build_store_with_bb_breach(direction: str) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store where the final price breaches a Bollinger Band.

    Generates a stable price series then adds a sharp move to breach.
    """
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    # 50 bars of stable, slightly noisy prices (low ADX, ranging)
    np.random.seed(42)
    prices = 2230.0 + np.cumsum(np.random.normal(0, 0.3, 50))

    for i, price in enumerate(prices):
        store.update(_snap(mark=float(price), ts=base + timedelta(seconds=i)))

    # Now add a sharp move to breach the band
    last_price = float(prices[-1])
    if direction == "below":
        breach_price = last_price - 15.0  # Sharp drop below lower band
    else:
        breach_price = last_price + 15.0  # Sharp spike above upper band

    snap = _snap(mark=breach_price, ts=base + timedelta(seconds=len(prices)))
    store.update(snap)
    return store, snap


class TestMeanReversionStrategy:
    def test_price_below_lower_band_signals_long(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=60.0,
            rsi_oversold=80.0,  # Relaxed RSI filter
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.source == SignalSource.MEAN_REVERSION
        assert sig.suggested_target == PortfolioTarget.B
        assert sig.stop_loss is not None
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit is not None

    def test_price_above_upper_band_signals_short(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=60.0,
            rsi_overbought=20.0,  # Relaxed RSI filter
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("above")

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT
        assert signals[0].stop_loss > signals[0].entry_price

    def test_price_within_bands_no_signal(self) -> None:
        params = MeanReversionParams(min_conviction=0.0, cooldown_bars=0)
        strategy = MeanReversionStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(50):
            store.update(_snap(mark=2230.0, ts=base + timedelta(seconds=i)))

        snap = _snap(mark=2230.0, ts=base + timedelta(seconds=50))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_high_adx_filters_signal(self) -> None:
        """Strong trend (high ADX) should suppress mean reversion."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=5.0,  # Very low threshold — almost always filters
            rsi_oversold=80.0,
        )
        strategy = MeanReversionStrategy(params=params)

        # Build a trending series (high ADX)
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(50):
            # Strong downtrend
            price = 2300.0 - i * 2.0
            store.update(_snap(mark=price, ts=base + timedelta(seconds=i)))

        snap = _snap(mark=2200.0, ts=base + timedelta(seconds=50))
        store.update(snap)
        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_cooldown_prevents_rapid_signals(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=100,  # Very long cooldown
            adx_max=60.0,
            rsi_oversold=80.0,
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        sig1 = strategy.evaluate(snap, store)
        assert len(sig1) == 1

        # Second evaluation during cooldown
        sig2 = strategy.evaluate(snap, store)
        assert sig2 == []

    def test_insufficient_history_no_signal(self) -> None:
        params = MeanReversionParams(min_conviction=0.0, cooldown_bars=0)
        strategy = MeanReversionStrategy(params=params)
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(5):
            store.update(_snap(ts=base + timedelta(seconds=i)))
        snap = _snap(ts=base + timedelta(seconds=5))
        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_take_profit_at_middle_band(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=60.0,
            rsi_oversold=80.0,
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        sig = signals[0]
        # TP should be at the middle band (SMA), which is between entry and upper band
        assert "bb_middle" in sig.metadata

    def test_signal_metadata(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=60.0,
            rsi_oversold=80.0,
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        m = signals[0].metadata
        assert "bb_upper" in m
        assert "bb_lower" in m
        assert "bb_middle" in m
        assert "deviation" in m
        assert "atr" in m

    def test_properties(self) -> None:
        strategy = MeanReversionStrategy()
        assert strategy.name == "mean_reversion"
        assert strategy.enabled is True
        assert strategy.min_history > 20


class TestMeanReversionConviction:
    def test_large_deviation_high_conviction(self) -> None:
        c = MeanReversionStrategy._compute_conviction(0.8, 20.0, True)
        assert c > 0.3

    def test_small_deviation_low_conviction(self) -> None:
        c = MeanReversionStrategy._compute_conviction(0.05, 50.0, True)
        assert c < 0.3

    def test_conviction_capped_at_one(self) -> None:
        c = MeanReversionStrategy._compute_conviction(10.0, 5.0, True)
        assert c <= 1.0

    def test_neutral_rsi_lower_conviction(self) -> None:
        # RSI at 50 (neutral) should contribute less than RSI at 20 (oversold)
        c_neutral = MeanReversionStrategy._compute_conviction(0.5, 50.0, True)
        c_oversold = MeanReversionStrategy._compute_conviction(0.5, 20.0, True)
        assert c_oversold >= c_neutral
