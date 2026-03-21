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
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a store simulating a liquidation cascade.

    First 2/3 of bars are calm, then the cascade hits hard in the final 1/3
    so that the lookback window sees the full impact.
    """
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    calm_bars = n_bars * 2 // 3
    cascade_bars = n_bars - calm_bars

    # Calm period: stable OI and price
    for i in range(calm_bars):
        store.update(_snap(
            mark=price_start, oi=oi_start, imbalance=0.0,
            ts=base + timedelta(seconds=i),
        ))

    # Cascade period: sharp OI drop and price move
    for i in range(cascade_bars):
        t = (i + 1) / cascade_bars
        oi = oi_start + (oi_end - oi_start) * t
        price = price_start + (price_end - price_start) * t
        store.update(_snap(
            mark=price, oi=oi, imbalance=imbalance * t,
            vol_1h=0.3 + 0.3 * t,
            ts=base + timedelta(seconds=calm_bars + i),
        ))

    final = _snap(
        mark=price_end, oi=oi_end, imbalance=imbalance,
        vol_1h=0.6,
        ts=base + timedelta(seconds=n_bars),
    )
    store.update(final)
    return store, final


class TestLiquidationCascadeStrategy:
    def test_oi_drop_with_price_dump_signals_long_fade(self) -> None:
        """Sharp OI drop + price dump -> fade with LONG."""
        params = LiquidationCascadeParams(
            oi_lookback=10,
            oi_drop_threshold_pct=1.5,
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
            oi_drop_threshold_pct=1.5,
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
            oi_drop_threshold_pct=2.0,
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
            oi_drop_threshold_pct=1.5,
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
            oi_drop_threshold_pct=1.5,
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
            oi_drop_threshold_pct=1.5,
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
