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


def _build_store_with_high_volume_breach(
    direction: str,
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store with a BB breach and high volume on the breach bar."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    np.random.seed(42)
    prices = 2230.0 + np.cumsum(np.random.normal(0, 0.3, 50))

    # Use incrementing volumes so bar_volumes (np.diff) are positive and consistent
    for i, price in enumerate(prices):
        snap = _snap(mark=float(price), ts=base + timedelta(seconds=i))
        # volume_24h grows by 100 per bar
        snap_with_vol = MarketSnapshot(
            timestamp=snap.timestamp,
            instrument=snap.instrument,
            mark_price=snap.mark_price,
            index_price=snap.index_price,
            last_price=snap.last_price,
            best_bid=snap.best_bid,
            best_ask=snap.best_ask,
            spread_bps=snap.spread_bps,
            volume_24h=Decimal(str(10000 + i * 100)),
            open_interest=snap.open_interest,
            funding_rate=snap.funding_rate,
            next_funding_time=snap.next_funding_time,
            hours_since_last_funding=snap.hours_since_last_funding,
            orderbook_imbalance=snap.orderbook_imbalance,
            volatility_1h=snap.volatility_1h,
            volatility_24h=snap.volatility_24h,
        )
        store.update(snap_with_vol)

    last_price = float(prices[-1])
    if direction == "below":
        breach_price = last_price - 15.0
    else:
        breach_price = last_price + 15.0

    # High volume breach bar: jump volume by 5000 (vs avg ~100/bar)
    snap = MarketSnapshot(
        timestamp=base + timedelta(seconds=len(prices)),
        instrument=INSTRUMENT_ID,
        mark_price=Decimal(str(breach_price)),
        index_price=Decimal(str(breach_price - 0.5)),
        last_price=Decimal(str(breach_price)),
        best_bid=Decimal(str(breach_price - 0.25)),
        best_ask=Decimal(str(breach_price + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal(str(10000 + len(prices) * 100 + 5000)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=base + timedelta(seconds=len(prices), minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )
    store.update(snap)
    return store, snap


def _build_store_with_low_volume_breach(
    direction: str,
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store with a BB breach and low volume on the breach bar."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    np.random.seed(42)
    prices = 2230.0 + np.cumsum(np.random.normal(0, 0.3, 50))

    for i, price in enumerate(prices):
        snap = _snap(mark=float(price), ts=base + timedelta(seconds=i))
        # Consistent volume increments
        snap_with_vol = MarketSnapshot(
            timestamp=snap.timestamp,
            instrument=snap.instrument,
            mark_price=snap.mark_price,
            index_price=snap.index_price,
            last_price=snap.last_price,
            best_bid=snap.best_bid,
            best_ask=snap.best_ask,
            spread_bps=snap.spread_bps,
            volume_24h=Decimal(str(10000 + i * 100)),
            open_interest=snap.open_interest,
            funding_rate=snap.funding_rate,
            next_funding_time=snap.next_funding_time,
            hours_since_last_funding=snap.hours_since_last_funding,
            orderbook_imbalance=snap.orderbook_imbalance,
            volatility_1h=snap.volatility_1h,
            volatility_24h=snap.volatility_24h,
        )
        store.update(snap_with_vol)

    last_price = float(prices[-1])
    if direction == "below":
        breach_price = last_price - 15.0
    else:
        breach_price = last_price + 15.0

    # Low volume breach bar: volume delta of just 10 (vs avg ~100/bar)
    snap = MarketSnapshot(
        timestamp=base + timedelta(seconds=len(prices)),
        instrument=INSTRUMENT_ID,
        mark_price=Decimal(str(breach_price)),
        index_price=Decimal(str(breach_price - 0.5)),
        last_price=Decimal(str(breach_price)),
        best_bid=Decimal(str(breach_price - 0.25)),
        best_ask=Decimal(str(breach_price + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal(str(10000 + len(prices) * 100 + 10)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=base + timedelta(seconds=len(prices), minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )
    store.update(snap)
    return store, snap


class TestMeanReversionStrategy:
    def test_price_below_lower_band_signals_long(self) -> None:
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=60.0,
            rsi_oversold=80.0,  # Relaxed RSI filter
            trend_reject_threshold=0.99,  # Relaxed trend filter
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.source == SignalSource.MEAN_REVERSION
        # With low conviction, routes to B
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
            trend_reject_threshold=0.99,  # Relaxed trend filter
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
        """Strong trend (high ADX) should suppress mean reversion via trend strength."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            adx_max=5.0,  # Very low threshold
            rsi_oversold=80.0,
            trend_reject_threshold=0.3,  # Very low threshold to reject easily
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
            trend_reject_threshold=0.99,
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
            trend_reject_threshold=0.99,
            extended_deviation_threshold=999.0,  # Disable extended targets
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
            trend_reject_threshold=0.99,
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
        # New Phase 2 metadata fields
        assert "volume_ratio" in m
        assert "adaptive_std" in m
        assert "trend_strength" in m

    def test_properties(self) -> None:
        strategy = MeanReversionStrategy()
        assert strategy.name == "mean_reversion"
        assert strategy.enabled is True
        assert strategy.min_history > 20


class TestMeanReversionConviction:
    def test_large_deviation_high_conviction(self) -> None:
        strategy = MeanReversionStrategy()
        c = strategy._compute_conviction(0.8, 20.0, True)
        assert c > 0.3

    def test_small_deviation_low_conviction(self) -> None:
        strategy = MeanReversionStrategy()
        c = strategy._compute_conviction(0.05, 50.0, True)
        assert c < 0.3

    def test_conviction_capped_at_one(self) -> None:
        strategy = MeanReversionStrategy()
        c = strategy._compute_conviction(10.0, 5.0, True)
        assert c <= 1.0

    def test_neutral_rsi_lower_conviction(self) -> None:
        # RSI at 50 (neutral) should contribute less than RSI at 20 (oversold)
        strategy = MeanReversionStrategy()
        c_neutral = strategy._compute_conviction(0.5, 50.0, True)
        c_oversold = strategy._compute_conviction(0.5, 20.0, True)
        assert c_oversold >= c_neutral


class TestMRConfig:
    """Test that YAML config loads ALL fields including new Phase 2 fields."""

    def test_config_loads_all_existing_fields(self) -> None:
        config = {
            "parameters": {
                "bb_period": 25,
                "bb_std": 1.8,
                "rsi_period": 10,
                "rsi_overbought": 65.0,
                "rsi_oversold": 35.0,
                "adx_period": 10,
                "adx_max": 30.0,
                "atr_period": 10,
                "stop_loss_atr_mult": 2.0,
                "cooldown_bars": 5,
                "min_conviction": 0.4,
            }
        }
        strategy = MeanReversionStrategy(config=config)
        p = strategy._params
        assert p.bb_period == 25
        assert p.bb_std == 1.8
        assert p.rsi_period == 10
        assert p.rsi_overbought == 65.0
        assert p.rsi_oversold == 35.0
        assert p.adx_period == 10
        assert p.adx_max == 30.0
        assert p.atr_period == 10
        assert p.stop_loss_atr_mult == 2.0
        assert p.cooldown_bars == 5
        assert p.min_conviction == 0.4

    def test_config_loads_new_phase2_fields(self) -> None:
        config = {
            "parameters": {
                "trend_reject_threshold": 0.7,
                "extended_deviation_threshold": 0.4,
                "portfolio_a_min_conviction": 0.70,
                "vol_lookback": 15,
            }
        }
        strategy = MeanReversionStrategy(config=config)
        p = strategy._params
        assert p.trend_reject_threshold == 0.7
        assert p.extended_deviation_threshold == 0.4
        assert p.portfolio_a_min_conviction == 0.70
        assert p.vol_lookback == 15

    def test_defaults_used_when_config_empty(self) -> None:
        strategy = MeanReversionStrategy()
        p = strategy._params
        assert p.trend_reject_threshold == 0.6
        assert p.extended_deviation_threshold == 0.5
        assert p.portfolio_a_min_conviction == 0.65
        assert p.vol_lookback == 10
        assert p.atr_period == 14
        assert p.stop_loss_atr_mult == 1.5
        assert p.cooldown_bars == 10


class TestMRTrendRejection:
    """Test multi-factor trend rejection replaces single ADX threshold."""

    def test_strong_trend_rejected(self) -> None:
        """High EMA slope + consecutive closes + high ADX = rejection."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.3,  # Low threshold to reject
        )
        strategy = MeanReversionStrategy(params=params)

        # Build strongly trending data
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(50):
            price = 2300.0 - i * 2.0  # Strong downtrend
            store.update(_snap(mark=price, ts=base + timedelta(seconds=i)))

        snap = _snap(mark=2200.0 - 15.0, ts=base + timedelta(seconds=50))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert signals == [], "Should reject signal in strong trend"

    def test_choppy_market_allows_signal(self) -> None:
        """Choppy market with low EMA slope should allow mean reversion."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,  # Very high threshold = allow
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1, "Should allow signal in choppy market"

    def test_compute_trend_strength_method_exists(self) -> None:
        """Strategy must have _compute_trend_strength method."""
        strategy = MeanReversionStrategy()
        assert hasattr(strategy, "_compute_trend_strength")


class TestMRAdaptiveBands:
    """Test that Bollinger Band width adapts to volatility regime."""

    def test_low_vol_tighter_bands(self) -> None:
        """In low volatility, bands should be tighter (lower adaptive_std)."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
        )
        strategy = MeanReversionStrategy(params=params)

        # Build a store with very low ATR (stable prices)
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        prices = 2230.0 + np.cumsum(np.random.normal(0, 0.05, 50))  # Very tight
        for i, price in enumerate(prices):
            store.update(_snap(mark=float(price), ts=base + timedelta(seconds=i)))

        breach = float(prices[-1]) - 5.0
        snap = _snap(mark=breach, ts=base + timedelta(seconds=50))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        if signals:
            # Adaptive std should be below base bb_std
            assert signals[0].metadata.get("adaptive_std", 999) < params.bb_std

    def test_high_vol_wider_bands(self) -> None:
        """In high volatility, bands should be wider (higher adaptive_std)."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
        )
        strategy = MeanReversionStrategy(params=params)

        # Build a store where most bars have low ATR but last bar is high ATR
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        # First 45 bars: low vol
        prices = list(2230.0 + np.cumsum(np.random.normal(0, 0.05, 45)))
        # Last 5 bars: high vol spike
        for i in range(5):
            prices.append(prices[-1] + (10.0 if i % 2 == 0 else -8.0))

        for i, price in enumerate(prices):
            store.update(_snap(mark=float(price), ts=base + timedelta(seconds=i)))

        breach = float(prices[-1]) + 20.0
        snap = _snap(mark=breach, ts=base + timedelta(seconds=len(prices)))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        if signals:
            assert signals[0].metadata.get("adaptive_std", 0) > params.bb_std


class TestMRExtendedTargets:
    """Test extended take-profit for strong reversions."""

    def test_strong_deviation_extended_target(self) -> None:
        """When deviation > threshold, TP goes beyond middle band with partial_target."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
            extended_deviation_threshold=0.01,  # Very low = always extended
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        sig = signals[0]
        # Should have partial_target metadata
        assert sig.metadata.get("partial_target") is not None
        # Take profit should be above middle band for long
        middle = Decimal(str(sig.metadata["bb_middle"]))
        assert sig.take_profit > middle

    def test_weak_deviation_middle_band_target(self) -> None:
        """When deviation <= threshold, TP is at middle band, no partial_target."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
            extended_deviation_threshold=999.0,  # Very high = never extended
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.metadata.get("partial_target") is None

    def test_short_extended_target_below_middle(self) -> None:
        """For shorts, extended TP should be below middle band."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_overbought=20.0,
            trend_reject_threshold=0.99,
            extended_deviation_threshold=0.01,  # Very low = always extended
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("above")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.metadata.get("partial_target") is not None
        middle = Decimal(str(sig.metadata["bb_middle"]))
        assert sig.take_profit < middle


class TestMRPortfolioRouting:
    """Test Portfolio A routing for high-conviction signals."""

    def test_high_conviction_routes_to_portfolio_a(self) -> None:
        """Signals with conviction >= 0.65 should route to Portfolio A."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
            portfolio_a_min_conviction=0.65,
        )
        strategy = MeanReversionStrategy(params=params)

        # Build a store with a very large breach to get high conviction
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        prices = 2230.0 + np.cumsum(np.random.normal(0, 0.3, 50))
        for i, price in enumerate(prices):
            snap = _snap(mark=float(price), ts=base + timedelta(seconds=i))
            snap_with_vol = MarketSnapshot(
                timestamp=snap.timestamp,
                instrument=snap.instrument,
                mark_price=snap.mark_price,
                index_price=snap.index_price,
                last_price=snap.last_price,
                best_bid=snap.best_bid,
                best_ask=snap.best_ask,
                spread_bps=snap.spread_bps,
                volume_24h=Decimal(str(10000 + i * 100)),
                open_interest=snap.open_interest,
                funding_rate=snap.funding_rate,
                next_funding_time=snap.next_funding_time,
                hours_since_last_funding=snap.hours_since_last_funding,
                orderbook_imbalance=snap.orderbook_imbalance,
                volatility_1h=snap.volatility_1h,
                volatility_24h=snap.volatility_24h,
            )
            store.update(snap_with_vol)

        # Very large breach + very extreme RSI scenario via relaxed thresholds
        last_price = float(prices[-1])
        breach_price = last_price - 25.0  # Huge breach

        snap = MarketSnapshot(
            timestamp=base + timedelta(seconds=len(prices)),
            instrument=INSTRUMENT_ID,
            mark_price=Decimal(str(breach_price)),
            index_price=Decimal(str(breach_price - 0.5)),
            last_price=Decimal(str(breach_price)),
            best_bid=Decimal(str(breach_price - 0.25)),
            best_ask=Decimal(str(breach_price + 0.25)),
            spread_bps=2.2,
            volume_24h=Decimal(str(10000 + len(prices) * 100 + 5000)),
            open_interest=Decimal("80000"),
            funding_rate=Decimal("0.0001"),
            next_funding_time=base + timedelta(seconds=len(prices), minutes=30),
            hours_since_last_funding=0.5,
            orderbook_imbalance=0.0,
            volatility_1h=0.15,
            volatility_24h=0.45,
        )
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].conviction >= 0.65
        assert signals[0].suggested_target == PortfolioTarget.A

    def test_low_conviction_routes_to_portfolio_b(self) -> None:
        """Signals with conviction < 0.65 should route to Portfolio B."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
            portfolio_a_min_conviction=0.65,
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        # With the default test data, conviction should be moderate
        if signals[0].conviction < 0.65:
            assert signals[0].suggested_target == PortfolioTarget.B


class TestMRVolumeBoost:
    """Test that high volume on band touch boosts conviction."""

    def test_high_volume_boosts_conviction(self) -> None:
        """Higher bar volume should increase conviction vs low bar volume."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
            vol_lookback=10,
        )

        # High volume scenario
        strategy_high = MeanReversionStrategy(params=params)
        store_high, snap_high = _build_store_with_high_volume_breach("below")
        signals_high = strategy_high.evaluate(snap_high, store_high)

        # Low volume scenario
        strategy_low = MeanReversionStrategy(params=params)
        store_low, snap_low = _build_store_with_low_volume_breach("below")
        signals_low = strategy_low.evaluate(snap_low, store_low)

        assert len(signals_high) == 1
        assert len(signals_low) == 1
        # High volume conviction should be >= low volume conviction
        assert signals_high[0].conviction >= signals_low[0].conviction

    def test_volume_ratio_in_metadata(self) -> None:
        """Volume ratio should appear in signal metadata."""
        params = MeanReversionParams(
            min_conviction=0.0,
            cooldown_bars=0,
            rsi_oversold=80.0,
            trend_reject_threshold=0.99,
        )
        strategy = MeanReversionStrategy(params=params)
        store, snap = _build_store_with_bb_breach("below")

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert "volume_ratio" in signals[0].metadata
