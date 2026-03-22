"""Tests for the liquidation cascade strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from libs.common.constants import INSTRUMENT_ID
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.liquidation_cascade import (
    LiquidationCascadeParams,
    LiquidationCascadeStrategy,
)


def _snap(
    mark: float = 2230.0,
    oi: float = 80000.0,
    imbalance: float = 0.0,
    vol_1h: float = 0.15,
    ts: datetime | None = None,
    volume_24h: float = 15000.0,
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
        volume_24h=Decimal(str(volume_24h)),
        open_interest=Decimal(str(oi)),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=imbalance,
        volatility_1h=vol_1h,
        volatility_24h=0.45,
    )


def _build_cascade_store(
    oi_start: float,
    oi_end: float,
    price_start: float,
    price_end: float,
    imbalance: float = -0.5,
    n_bars: int = 30,
    volume_base: float = 15000.0,
    volume_surge_mult: float = 3.0,
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store simulating a liquidation cascade.

    First 2/3 of bars are calm, then the cascade hits hard in the final 1/3
    so that the lookback window sees the full impact.

    Volume pattern: steady increments throughout, with the final bar having
    a volume_surge_mult-sized spike relative to average bar volume.
    This ensures bar_volumes[-1] / mean(bar_volumes[-vol_lookback:]) >= surge ratio.
    """
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    calm_bars = n_bars * 2 // 3
    cascade_bars = n_bars - calm_bars

    # Base volume increment per bar
    base_vol_incr = 100.0

    # Calm period: stable OI and price, steady volume increments
    cumulative_vol = volume_base
    for i in range(calm_bars):
        cumulative_vol += base_vol_incr
        store.update(_snap(
            mark=price_start, oi=oi_start, imbalance=0.0,
            ts=base + timedelta(seconds=i),
            volume_24h=cumulative_vol,
        ))

    # Cascade period: sharp OI drop and price move, steady volume
    for i in range(cascade_bars):
        t = (i + 1) / cascade_bars
        oi = oi_start + (oi_end - oi_start) * t
        price = price_start + (price_end - price_start) * t
        cumulative_vol += base_vol_incr
        store.update(_snap(
            mark=price, oi=oi, imbalance=imbalance * t,
            vol_1h=0.3 + 0.3 * t,
            ts=base + timedelta(seconds=calm_bars + i),
            volume_24h=cumulative_vol,
        ))

    # Final bar: volume spike (volume_surge_mult * normal increment)
    cumulative_vol += base_vol_incr * volume_surge_mult
    final = _snap(
        mark=price_end, oi=oi_end, imbalance=imbalance,
        vol_1h=0.6,
        ts=base + timedelta(seconds=n_bars),
        volume_24h=cumulative_vol,
    )
    store.update(final)
    return store, final


class TestLiquidationCascadeStrategy:
    def test_oi_drop_with_price_dump_signals_long_fade(self) -> None:
        """Sharp OI drop + price dump -> fade with LONG."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=1.5,
            imbalance_threshold=0.2,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=76000,  # 5% OI drop
            price_start=2250, price_end=2200,  # Price dumped
            imbalance=-0.5,  # Sell-heavy book
        )

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.source == SignalSource.LIQUIDATION_CASCADE
        assert sig.suggested_target == PortfolioTarget.A
        assert sig.metadata["mode"] == "fade"
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit > sig.entry_price

    def test_oi_drop_with_price_pump_signals_short_fade(self) -> None:
        """Sharp OI drop + price pump -> short squeeze exhaustion fade."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=1.5,
            imbalance_threshold=0.2,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=76000,
            price_start=2200, price_end=2250,  # Price pumped
            imbalance=0.5,  # Buy-heavy book
        )

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT
        assert signals[0].metadata["mode"] == "fade"

    def test_no_signal_without_oi_drop(self) -> None:
        """Stable OI should produce no signal."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=2.0,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=79500,  # Only 0.6% drop
            price_start=2230, price_end=2220,
        )

        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_insufficient_history_no_signal(self) -> None:
        params = LiquidationCascadeParams(min_conviction=0.0, cooldown_bars=0)
        strategy = LiquidationCascadeStrategy(params=params)

        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        for i in range(3):
            store.update(_snap(ts=base + timedelta(seconds=i)))
        snap = _snap(ts=base + timedelta(seconds=3))
        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_cooldown_prevents_rapid_signals(self) -> None:
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=1.5,
            imbalance_threshold=0.2,
            min_conviction=0.0,
            cooldown_bars=100,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=76000,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        sig1 = strategy.evaluate(snap, store)
        assert len(sig1) == 1

        sig2 = strategy.evaluate(snap, store)
        assert sig2 == []

    def test_time_horizon_short(self) -> None:
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=1.5,
            imbalance_threshold=0.2,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=76000,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].time_horizon <= timedelta(hours=2)

    def test_signal_metadata(self) -> None:
        params = LiquidationCascadeParams(
            oi_lookback=10,
            tier1_min_oi_drop_pct=1.5,
            imbalance_threshold=0.2,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=76000,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        m = signals[0].metadata
        assert "oi_change_pct" in m
        assert "price_change_pct" in m
        assert "orderbook_imbalance" in m
        assert "mode" in m
        assert "atr" in m

    def test_properties(self) -> None:
        strategy = LiquidationCascadeStrategy()
        assert strategy.name == "liquidation_cascade"
        assert strategy.enabled is True
        assert strategy.min_history > 10


class TestOIChangeComputation:
    def test_computes_percentage_drop(self) -> None:
        ois = np.array([100.0] * 10 + [95.0], dtype=np.float64)
        pct = LiquidationCascadeStrategy._compute_oi_change_pct(ois, 10)
        assert pct is not None
        assert abs(pct - (-5.0)) < 0.01

    def test_insufficient_data(self) -> None:
        ois = np.array([100.0], dtype=np.float64)
        assert LiquidationCascadeStrategy._compute_oi_change_pct(ois, 10) is None

    def test_zero_oi_returns_none(self) -> None:
        ois = np.array([0.0] * 11, dtype=np.float64)
        assert LiquidationCascadeStrategy._compute_oi_change_pct(ois, 10) is None


class TestCascadeConviction:
    def test_severe_cascade_high_conviction(self) -> None:
        c = LiquidationCascadeStrategy._compute_conviction(
            oi_change_pct=-8.0,
            price_change_pct=-5.0,
            imbalance=-0.7,
            volatility_1h=0.8,
            params=LiquidationCascadeParams(),
        )
        assert c > 0.5

    def test_mild_event_low_conviction(self) -> None:
        c = LiquidationCascadeStrategy._compute_conviction(
            oi_change_pct=-2.0,
            price_change_pct=-0.5,
            imbalance=-0.1,
            volatility_1h=0.1,
            params=LiquidationCascadeParams(),
        )
        assert c < 0.5

    def test_conviction_capped_at_one(self) -> None:
        c = LiquidationCascadeStrategy._compute_conviction(
            oi_change_pct=-50.0,
            price_change_pct=-20.0,
            imbalance=-1.0,
            volatility_1h=2.0,
            params=LiquidationCascadeParams(),
        )
        assert c <= 1.0


class TestTierClassification:
    """Tests for graduated cascade response tiers (LIQ-01)."""

    def test_tier1_3pct_oi_drop_uses_tier1_stops(self) -> None:
        """OI drop of 3% classifies as Tier 1 with tier1 stop/TP ATR mults."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        # 3% OI drop: (80000 - 77600) / 80000 = 3%
        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=77600,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].metadata["tier"] == 1

    def test_tier2_6pct_oi_drop_uses_tier2_stops(self) -> None:
        """OI drop of 6% classifies as Tier 2 with tier2 stop/TP ATR mults."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        # 6% OI drop: (80000 - 75200) / 80000 = 6%
        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=75200,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].metadata["tier"] == 2

    def test_tier3_10pct_oi_drop_uses_tier3_stops(self) -> None:
        """OI drop of 10% classifies as Tier 3 with tier3 stop/TP ATR mults."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        # 10% OI drop: (80000 - 72000) / 80000 = 10%
        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=72000,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert signals[0].metadata["tier"] == 3

    def test_tier_boundary_4pct_is_tier2(self) -> None:
        """Exactly 4.0% drop classifies as Tier 2 (boundary: [2%,4%)=T1, [4%,8%)=T2)."""
        tier = LiquidationCascadeStrategy._classify_tier(4.0)
        assert tier == 2

    def test_tier_boundary_8pct_is_tier3(self) -> None:
        """Exactly 8.0% drop classifies as Tier 3."""
        tier = LiquidationCascadeStrategy._classify_tier(8.0)
        assert tier == 3

    def test_tier3_higher_conviction_than_tier1(self) -> None:
        """Tier 3 signal has higher base conviction than Tier 1 for same inputs."""
        params = LiquidationCascadeParams(
            min_conviction=0.0,
        )

        c1 = LiquidationCascadeStrategy._compute_conviction(
            oi_change_pct=-3.0,
            price_change_pct=-2.0,
            imbalance=-0.4,
            volatility_1h=0.3,
            params=params,
            tier=1,
        )

        c3 = LiquidationCascadeStrategy._compute_conviction(
            oi_change_pct=-3.0,
            price_change_pct=-2.0,
            imbalance=-0.4,
            volatility_1h=0.3,
            params=params,
            tier=3,
        )

        assert c3 > c1


class TestVolumeSurgeConfirmation:
    """Tests for volume surge confirmation gate (LIQ-02)."""

    def test_volume_surge_below_threshold_no_signal(self) -> None:
        """OI drop of 3% with volume surge < 1.5x average returns no signal."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
            vol_surge_min_ratio=1.5,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        # Build store with NO volume surge (flat volume)
        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=77600,  # 3% drop
            price_start=2250, price_end=2200,
            imbalance=-0.5,
            volume_surge_mult=1.0,  # No surge, same as calm period
        )

        signals = strategy.evaluate(snap, store)
        assert signals == []

    def test_volume_surge_above_threshold_signal(self) -> None:
        """OI drop of 3% with volume surge >= 1.5x average returns signal."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
            vol_surge_min_ratio=1.5,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        # Build store with volume surge (3x normal during cascade)
        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=77600,  # 3% drop
            price_start=2250, price_end=2200,
            imbalance=-0.5,
            volume_surge_mult=3.0,  # 3x surge
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1

    def test_signal_metadata_contains_vol_surge_ratio(self) -> None:
        """Signal metadata contains 'vol_surge_ratio' key."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=77600,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert "vol_surge_ratio" in signals[0].metadata
        assert isinstance(signals[0].metadata["vol_surge_ratio"], float)

    def test_signal_metadata_contains_tier(self) -> None:
        """Signal metadata contains 'tier' key with integer value."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            min_conviction=0.0,
            cooldown_bars=0,
            imbalance_threshold=0.2,
        )
        strategy = LiquidationCascadeStrategy(params=params)

        store, snap = _build_cascade_store(
            oi_start=80000, oi_end=77600,
            price_start=2250, price_end=2200,
            imbalance=-0.5,
        )

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1
        assert "tier" in signals[0].metadata
        assert signals[0].metadata["tier"] in (1, 2, 3)
