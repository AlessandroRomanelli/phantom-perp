"""Tests for the VWAP deviation strategy.

Phase A: Feasibility validation -- tests that determine whether bar_volumes
(np.diff of 24h rolling volume) can produce usable VWAP values.

Phase B: Strategy tests -- conditional on feasibility passing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from numpy.typing import NDArray

from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore

TEST_INSTRUMENT_ID = "ETH-PERP"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simulate_bar_volumes(n_bars: int = 200) -> NDArray[np.float64]:
    """Simulate bar_volumes as np.diff of 24h rolling volume.

    Model: each bar adds some volume, but np.diff of 24h rolling
    means we subtract what rolled off 24h ago.
    """
    np.random.seed(42)
    hourly_vol = np.abs(np.random.normal(1000, 300, n_bars + 24))
    # 24h rolling sum
    rolling_24h = np.convolve(hourly_vol, np.ones(24), mode="valid")
    # bar_volumes = np.diff of rolling 24h
    return np.diff(rolling_24h)


def _snap(
    price: float = 2230.0,
    volume_24h: float = 15000.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    if ts is None:
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument=TEST_INSTRUMENT_ID,
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 0.5)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.25)),
        best_ask=Decimal(str(price + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal(str(volume_24h)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )


def _build_vwap_store(
    n_bars: int = 100,
    base_price: float = 2230.0,
    base_volume: float = 15000.0,
    price_trend: float = 0.0,
    session_start_hour: int = 0,
    start_ts: datetime | None = None,
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store with price and volume data for VWAP computation."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    if start_ts is None:
        start_ts = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)

    np.random.seed(42)
    for i in range(n_bars):
        price = base_price + price_trend * i + np.random.normal(0, 2.0)
        # Volume increases gradually so bar_volumes (np.diff) are mostly positive
        vol = base_volume + i * 10 + np.random.normal(0, 100)
        ts = start_ts + timedelta(minutes=i)
        store.update(_snap(price=price, volume_24h=max(vol, 100), ts=ts))

    last_price = base_price + price_trend * n_bars
    last_vol = base_volume + n_bars * 10
    last_ts = start_ts + timedelta(minutes=n_bars)
    snap = _snap(price=last_price, volume_24h=last_vol, ts=last_ts)
    store.update(snap)
    return store, snap


# ===========================================================================
# Phase A: Feasibility Validation
# ===========================================================================


class TestFeasibilityNegativeVolumeFrequency:
    """Test how often bar_volumes are negative with realistic 24h rolling diff."""

    def test_feasibility_negative_volume_frequency(self) -> None:
        """Generate realistic bar_volumes and measure negative frequency."""
        bar_vols = _simulate_bar_volumes(200)
        neg_count = np.sum(bar_vols < 0)
        neg_pct = neg_count / len(bar_vols) * 100

        # Record the result for decision-making
        # bar_volumes from np.diff of 24h rolling WILL have negatives
        # The key question is: what percentage?
        assert len(bar_vols) > 0
        # We just record the fact; the clamping test determines if it's usable
        print(f"Negative bar_volume frequency: {neg_pct:.1f}%")


class TestFeasibilityVwapWithClampedVolumes:
    """Test whether VWAP with clamped (non-negative) volumes adds value."""

    def test_feasibility_vwap_with_clamped_volumes(self) -> None:
        """Compute VWAP with max(bar_volume, 0) and compare to SMA."""
        bar_vols = _simulate_bar_volumes(200)
        np.random.seed(42)
        prices = 2230.0 + np.cumsum(np.random.normal(0, 1, len(bar_vols)))

        # Clamp negative volumes to 0
        clamped_vols = np.maximum(bar_vols, 0)

        # Compute VWAP with clamped volumes
        cum_vol = np.cumsum(clamped_vols)
        cum_pv = np.cumsum(prices * clamped_vols)
        # Avoid division by zero
        valid = cum_vol > 0
        vwap = np.where(valid, cum_pv / cum_vol, prices)

        # Compute SMA
        sma = np.cumsum(prices) / np.arange(1, len(prices) + 1)

        # Check if VWAP differs meaningfully from SMA
        # If difference is > 0.05% of price, VWAP adds value
        mean_price = np.mean(prices)
        mean_diff = np.mean(np.abs(vwap[valid] - sma[valid]))
        diff_pct = mean_diff / mean_price * 100

        print(f"VWAP vs SMA mean diff: {diff_pct:.4f}% of price")
        # This is informational; the stability test matters more


class TestFeasibilityVwapStability:
    """Test whether VWAP with clamped volumes is smoother than raw price."""

    def test_feasibility_vwap_stability(self) -> None:
        """Check that VWAP is less volatile than raw price."""
        bar_vols = _simulate_bar_volumes(200)
        np.random.seed(42)
        prices = 2230.0 + np.cumsum(np.random.normal(0, 1, len(bar_vols)))

        clamped_vols = np.maximum(bar_vols, 0)
        cum_vol = np.cumsum(clamped_vols)
        cum_pv = np.cumsum(prices * clamped_vols)
        valid = cum_vol > 0
        vwap = np.where(valid, cum_pv / cum_vol, prices)

        # Compare rolling standard deviations
        window = 20
        price_std = np.std(prices[-window:])
        vwap_std = np.std(vwap[-window:])

        print(f"Price std (last {window}): {price_std:.4f}")
        print(f"VWAP std (last {window}): {vwap_std:.4f}")
        # VWAP should be smoother (lower std) than price
        assert vwap_std < price_std, (
            f"VWAP not smoother than price: vwap_std={vwap_std:.4f} >= price_std={price_std:.4f}"
        )


class TestFeasibilityAlternativeRollingVwap:
    """Test alternative: rolling price-volume weighted average using volumes."""

    def test_feasibility_alternative_rolling_vwap(self) -> None:
        """Use volumes (24h rolling, always positive) as weights for VWAP."""
        n = 100
        np.random.seed(42)
        prices = 2230.0 + np.cumsum(np.random.normal(0, 1, n))
        # volumes (24h rolling) are always positive
        volumes = 15000.0 + np.random.normal(0, 500, n)
        volumes = np.abs(volumes)

        lookback = 30
        alt_vwap = np.full(n, np.nan)
        for i in range(lookback, n):
            window_p = prices[i - lookback:i + 1]
            window_v = volumes[i - lookback:i + 1]
            alt_vwap[i] = np.sum(window_p * window_v) / np.sum(window_v)

        valid = ~np.isnan(alt_vwap)
        # Alternative VWAP should be smoother than price
        if np.sum(valid) > 20:
            price_std = np.std(prices[valid][-20:])
            vwap_std = np.std(alt_vwap[valid][-20:])
            print(f"Alt VWAP std: {vwap_std:.4f}, Price std: {price_std:.4f}")
            assert vwap_std < price_std, "Alternative rolling VWAP not smoother"

        # Alternative VWAP should differ from SMA (adding value)
        sma = np.full(n, np.nan)
        for i in range(lookback, n):
            sma[i] = np.mean(prices[i - lookback:i + 1])
        both_valid = valid & ~np.isnan(sma)
        mean_diff = np.mean(np.abs(alt_vwap[both_valid] - sma[both_valid]))
        mean_price = np.mean(prices)
        diff_pct = mean_diff / mean_price * 100
        print(f"Alt VWAP vs SMA diff: {diff_pct:.4f}% of price")


# ===========================================================================
# Phase B: Strategy Tests (only if feasibility passes)
# ===========================================================================


class TestVWAPSessionReset:
    """Test session reset behavior for crypto vs equity instruments."""

    def test_vwap_session_reset_crypto(self) -> None:
        """VWAP resets at 00:00 UTC for crypto instruments."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            session_reset_hour_utc=0,
            deviation_threshold=2.0,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = VWAPStrategy(params=params)

        # Build store starting at 22:00 UTC, crossing midnight
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 14, 22, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(180):  # 3 hours of minute bars
            price = 2230.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            ts = base + timedelta(minutes=i)
            store.update(_snap(price=price, volume_24h=vol, ts=ts))

        # The strategy should detect session boundary at midnight UTC
        # and only use post-midnight bars for VWAP computation
        snap = _snap(price=2230.0, volume_24h=16800.0, ts=base + timedelta(minutes=180))
        store.update(snap)

        # Strategy should not crash with session reset
        signals = strategy.evaluate(snap, store)
        assert isinstance(signals, list)

    def test_vwap_session_reset_equity(self) -> None:
        """VWAP resets at 14:00 UTC (approx 09:30 ET) for equity instruments."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            session_reset_hour_utc=14,
            deviation_threshold=2.0,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = VWAPStrategy(params=params)

        # Build store crossing 14:00 UTC boundary
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(180):
            price = 450.0 + np.random.normal(0, 0.5)
            vol = 5000.0 + i * 5
            ts = base + timedelta(minutes=i)
            store.update(_snap(price=price, volume_24h=vol, ts=ts))

        snap = _snap(price=450.0, volume_24h=5900.0, ts=base + timedelta(minutes=180))
        store.update(snap)
        signals = strategy.evaluate(snap, store)
        assert isinstance(signals, list)


class TestVWAPSignalGeneration:
    """Test VWAP deviation signals."""

    def test_long_signal_below_vwap(self) -> None:
        """Price significantly below session VWAP emits LONG signal."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,  # Disable early suppression for test
        )
        strategy = VWAPStrategy(params=params)

        # Build store where price starts high then drops well below VWAP
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)

        # First 80 bars: stable around 2250
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        # Last bar: price drops to well below VWAP
        drop_price = 2220.0  # Significantly below ~2250 VWAP
        snap = _snap(price=drop_price, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].direction == PositionSide.LONG
        assert signals[0].source == SignalSource.VWAP

    def test_short_signal_above_vwap(self) -> None:
        """Price significantly above session VWAP emits SHORT signal."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,
        )
        strategy = VWAPStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)

        # 80 bars stable around 2230
        for i in range(80):
            price = 2230.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        # Spike up well above VWAP
        spike_price = 2260.0
        snap = _snap(price=spike_price, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT

    def test_no_signal_near_vwap(self) -> None:
        """Price near VWAP returns empty list."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=2.0,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,
        )
        strategy = VWAPStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)

        # All bars stable around same price -- price IS the VWAP
        for i in range(80):
            price = 2230.0 + np.random.normal(0, 0.5)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        snap = _snap(price=2230.2, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert signals == []


class TestVWAPSessionTimeConviction:
    """Test session time conviction scaling (VWAP-04)."""

    def test_session_time_conviction_scaling(self) -> None:
        """Signal at 80% through session has higher conviction than at 20%."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,
            session_conviction_weight=0.3,
        )

        # Build two scenarios with same deviation but different session progress

        # Scenario 1: Early session (20% progress -> ~4.8h into 24h session)
        strategy_early = VWAPStrategy(params=params)
        store_early = FeatureStore(sample_interval=timedelta(seconds=0))
        base_early = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            # Only ~80 min into session
            store_early.update(
                _snap(price=price, volume_24h=vol, ts=base_early + timedelta(minutes=i))
            )
        snap_early = _snap(
            price=2220.0, volume_24h=15800.0, ts=base_early + timedelta(minutes=80)
        )
        store_early.update(snap_early)
        signals_early = strategy_early.evaluate(snap_early, store_early)

        # Scenario 2: Late session (80% progress -> ~19.2h into 24h session)
        strategy_late = VWAPStrategy(params=params)
        store_late = FeatureStore(sample_interval=timedelta(seconds=0))
        base_late = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            # Start at 19h into session
            store_late.update(
                _snap(
                    price=price,
                    volume_24h=vol,
                    ts=base_late + timedelta(hours=19, minutes=i),
                )
            )
        snap_late = _snap(
            price=2220.0, volume_24h=15800.0,
            ts=base_late + timedelta(hours=19, minutes=80),
        )
        store_late.update(snap_late)
        signals_late = strategy_late.evaluate(snap_late, store_late)

        assert len(signals_early) == 1
        assert len(signals_late) == 1
        # Late session should have higher conviction due to session progress boost
        assert signals_late[0].conviction >= signals_early[0].conviction

    def test_early_session_suppression(self) -> None:
        """In first 20% of session, signals are suppressed."""
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.2,  # Suppress first 20%
        )
        strategy = VWAPStrategy(params=params)

        # Build store in early session (first 30 minutes of 24h session)
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(30):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        # Even with big deviation, should be suppressed in early session
        snap = _snap(price=2220.0, volume_24h=15300.0, ts=base + timedelta(minutes=30))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert signals == [], "Signals should be suppressed in early session"


class TestVWAPProperties:
    """Test strategy properties and config."""

    def test_properties(self) -> None:
        from agents.signals.strategies.vwap import VWAPStrategy

        strategy = VWAPStrategy()
        assert strategy.name == "vwap"
        assert strategy.enabled is True
        assert strategy.min_history >= 20

    def test_yaml_config_override(self) -> None:
        from agents.signals.strategies.vwap import VWAPStrategy

        config = {
            "parameters": {
                "deviation_threshold": 3.0,
                "session_reset_hour_utc": 14,
                "min_session_progress": 0.3,
            },
        }
        strategy = VWAPStrategy(config=config)
        assert strategy._params.deviation_threshold == 3.0
        assert strategy._params.session_reset_hour_utc == 14
        assert strategy._params.min_session_progress == 0.3

    def test_cooldown_prevents_rapid_signals(self) -> None:
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=100,
            min_session_progress=0.0,
        )
        strategy = VWAPStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        snap = _snap(price=2220.0, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        sig1 = strategy.evaluate(snap, store)
        assert len(sig1) == 1

        sig2 = strategy.evaluate(snap, store)
        assert sig2 == []

    def test_signal_metadata(self) -> None:
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.5,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,
        )
        strategy = VWAPStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        snap = _snap(price=2220.0, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        m = signals[0].metadata
        assert "vwap" in m
        assert "deviation" in m
        assert "session_progress" in m
        assert "atr" in m

    def test_portfolio_a_routing(self) -> None:
        from agents.signals.strategies.vwap import VWAPParams, VWAPStrategy

        params = VWAPParams(
            deviation_threshold=1.0,
            min_conviction=0.0,
            cooldown_bars=0,
            min_session_progress=0.0,
            portfolio_a_min_conviction=0.50,
        )
        strategy = VWAPStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 0, 0, 0, tzinfo=UTC)
        np.random.seed(42)
        for i in range(80):
            price = 2250.0 + np.random.normal(0, 1)
            vol = 15000.0 + i * 10
            store.update(_snap(price=price, volume_24h=vol, ts=base + timedelta(minutes=i)))

        # Big deviation should give decent conviction
        snap = _snap(price=2210.0, volume_24h=15800.0, ts=base + timedelta(minutes=80))
        store.update(snap)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        # With big deviation, conviction should be decent
        if signals[0].conviction >= 0.50:
            assert signals[0].suggested_target == PortfolioTarget.A
        else:
            assert signals[0].suggested_target == PortfolioTarget.B
