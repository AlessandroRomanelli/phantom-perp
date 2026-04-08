"""Tests for the feature store."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore

TEST_INSTRUMENT_ID = "ETH-PERP"


def _snap(
    ts: datetime,
    mark: float = 2230.0,
    funding: float = 0.0001,
    instrument: str = TEST_INSTRUMENT_ID,
) -> MarketSnapshot:
    """Create a minimal MarketSnapshot for testing."""
    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark - 0.5)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.25)),
        best_ask=Decimal(str(mark + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal("15000"),
        open_interest=Decimal("80000"),
        funding_rate=Decimal(str(funding)),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )


class TestFeatureStore:
    def test_first_snapshot_always_sampled(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        result = store.update(_snap(ts))
        assert result is True
        assert store.sample_count == 1

    def test_respects_sample_interval(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

        store.update(_snap(base))
        # 30s later — should be skipped
        assert store.update(_snap(base + timedelta(seconds=30))) is False
        assert store.sample_count == 1
        # 61s later — should be sampled
        assert store.update(_snap(base + timedelta(seconds=61))) is True
        assert store.sample_count == 2

    def test_closes_array(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        for i in range(5):
            store.update(_snap(base + timedelta(seconds=i), mark=2230 + i))
        closes = store.closes
        assert len(closes) == 5
        np.testing.assert_array_almost_equal(
            closes, [2230, 2231, 2232, 2233, 2234]
        )

    def test_tracks_interval_high_low(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

        store.update(_snap(base, mark=100.0))
        # Sub-interval ticks (not sampled but tracked)
        store.update(_snap(base + timedelta(seconds=10), mark=105.0))
        store.update(_snap(base + timedelta(seconds=20), mark=95.0))
        store.update(_snap(base + timedelta(seconds=30), mark=102.0))
        # Next sample
        store.update(_snap(base + timedelta(seconds=61), mark=102.0))

        assert store.sample_count == 2
        assert store.highs[1] == 105.0  # Highest between samples
        assert store.lows[1] == 95.0  # Lowest between samples

    def test_funding_rate_accumulated(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        store.update(_snap(base, funding=0.0001))
        store.update(_snap(base + timedelta(seconds=1), funding=0.0002))
        store.update(_snap(base + timedelta(seconds=2), funding=0.0003))

        assert store.funding_rate_count == 3
        np.testing.assert_array_almost_equal(
            store.funding_rates, [0.0001, 0.0002, 0.0003]
        )

    def test_funding_rate_deduplication(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        # Same rate twice — only recorded once
        store.update(_snap(base, funding=0.0001))
        store.update(_snap(base + timedelta(seconds=1), funding=0.0001))
        assert store.funding_rate_count == 1

    def test_max_samples_enforced(self) -> None:
        store = FeatureStore(max_samples=10, sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        for i in range(20):
            store.update(_snap(base + timedelta(seconds=i), mark=2230 + i))
        assert store.sample_count == 10
        # Should retain the most recent
        assert store.latest_close == 2249.0

    def test_zero_funding_skipped(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        store.update(_snap(base, funding=0.0))
        assert store.funding_rate_count == 0

    def test_latest_close_empty(self) -> None:
        store = FeatureStore()
        assert store.latest_close is None
        assert store.latest_timestamp is None


def _snap_with_volume(
    ts: datetime,
    mark: float = 2230.0,
    volume_24h: float = 15000.0,
    candle_volume_1m: float = 0.0,
) -> MarketSnapshot:
    """Create a MarketSnapshot with specific volume fields for testing."""
    return MarketSnapshot(
        timestamp=ts,
        instrument=TEST_INSTRUMENT_ID,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark - 0.5)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.25)),
        best_ask=Decimal(str(mark + 0.25)),
        spread_bps=2.2,
        volume_24h=Decimal(str(volume_24h)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.15,
        volatility_24h=0.45,
        candle_volume_1m=Decimal(str(candle_volume_1m)),
    )


class TestTimestamps:
    def test_timestamps_empty(self) -> None:
        store = FeatureStore()
        result = store.timestamps
        assert result.shape == (0,)
        assert result.dtype == np.float64

    def test_timestamps_returns_epoch_floats(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        ts_list = [base, base + timedelta(seconds=60), base + timedelta(seconds=120)]
        for ts in ts_list:
            store.update(_snap_with_volume(ts))

        result = store.timestamps
        assert result.shape == (3,)
        assert result.dtype == np.float64
        for i, ts in enumerate(ts_list):
            assert result[i] == pytest.approx(ts.timestamp())


class TestBarVolumes:
    def test_bar_volumes_empty(self) -> None:
        store = FeatureStore()
        result = store.bar_volumes
        assert result.shape == (0,)

    def test_bar_volumes_one_sample(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        store.update(_snap_with_volume(base, candle_volume_1m=100.0))
        result = store.bar_volumes
        # One sample → one bar_volume entry (candle volume, not a diff)
        assert result.shape == (1,)
        assert result[0] == pytest.approx(100.0)

    def test_bar_volumes_from_candle_data(self) -> None:
        """bar_volumes returns true per-bar candle volumes, never negative."""
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        candle_vols = [100.0, 150.0, 200.0]
        for i, vol in enumerate(candle_vols):
            store.update(_snap_with_volume(base + timedelta(seconds=i), candle_volume_1m=vol))

        result = store.bar_volumes
        np.testing.assert_array_almost_equal(result, [100.0, 150.0, 200.0])

    def test_bar_volumes_length(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        for i in range(5):
            store.update(_snap_with_volume(base + timedelta(seconds=i), candle_volume_1m=50.0 + i * 10))

        # bar_volumes length matches sample_count (one per sample, not N-1 diffs)
        assert len(store.bar_volumes) == 5
