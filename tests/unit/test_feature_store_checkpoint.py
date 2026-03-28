"""Tests for FeatureStore checkpoint/restore persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import orjson
import pytest

from agents.signals.feature_store import FeatureStore
from agents.signals.main import _restore_store
from libs.common.models.market_snapshot import MarketSnapshot


def _make_snapshot(
    instrument: str = "ETH-PERP",
    price: float = 2500.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """Build a minimal MarketSnapshot for testing."""
    if ts is None:
        ts = datetime.now(tz=timezone.utc)
    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument,
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 1)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.5)),
        best_ask=Decimal(str(price + 0.5)),
        spread_bps=4.0,
        volume_24h=Decimal("1000000"),
        open_interest=Decimal("500000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(hours=1),
        hours_since_last_funding=7.0,
        orderbook_imbalance=0.15,
        volatility_1h=0.02,
        volatility_24h=0.03,
    )


class TestToCheckpoint:
    def test_empty_store(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        cp = store.to_checkpoint()
        assert cp["version"] == 1
        assert cp["sample_interval_seconds"] == 60.0
        assert cp["last_sample_time"] is None
        assert cp["closes"] == []
        assert cp["interval_low"] is None  # float("inf") -> None

    def test_populated_store(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=1))
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            snap = _make_snapshot(price=100.0 + i, ts=base + timedelta(seconds=i))
            store.update(snap)

        cp = store.to_checkpoint()
        assert len(cp["closes"]) == 5
        assert len(cp["timestamps"]) == 5
        assert cp["closes"][0] == 100.0
        assert cp["closes"][-1] == 104.0
        assert cp["last_sample_time"] is not None

    def test_orjson_roundtrip(self) -> None:
        """Checkpoint survives orjson.dumps → orjson.loads."""
        store = FeatureStore(sample_interval=timedelta(seconds=1))
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            store.update(_make_snapshot(price=50.0 + i, ts=base + timedelta(seconds=i)))

        raw = orjson.dumps(store.to_checkpoint())
        data = orjson.loads(raw)
        assert data["version"] == 1
        assert len(data["closes"]) == 3


class TestFromCheckpoint:
    def test_roundtrip(self) -> None:
        """Populate → checkpoint → restore → all fields match."""
        interval = timedelta(seconds=1)
        original = FeatureStore(sample_interval=interval)
        base = datetime(2025, 6, 1, tzinfo=timezone.utc)
        for i in range(10):
            original.update(
                _make_snapshot(price=200.0 + i, ts=base + timedelta(seconds=i))
            )

        cp = orjson.loads(orjson.dumps(original.to_checkpoint()))
        restored = FeatureStore.from_checkpoint(cp, sample_interval=interval)

        assert restored.sample_count == original.sample_count
        assert list(restored._closes) == list(original._closes)
        assert list(restored._highs) == list(original._highs)
        assert list(restored._lows) == list(original._lows)
        assert list(restored._index_prices) == list(original._index_prices)
        assert list(restored._volumes) == list(original._volumes)
        assert list(restored._open_interests) == list(original._open_interests)
        assert list(restored._orderbook_imbalances) == list(original._orderbook_imbalances)
        assert list(restored._funding_rates) == list(original._funding_rates)
        assert list(restored._timestamps) == list(original._timestamps)
        assert restored._interval_high == original._interval_high
        assert restored._interval_low == original._interval_low
        assert restored._last_sample_time == original._last_sample_time

    def test_empty_roundtrip(self) -> None:
        interval = timedelta(seconds=60)
        original = FeatureStore(sample_interval=interval)
        cp = orjson.loads(orjson.dumps(original.to_checkpoint()))
        restored = FeatureStore.from_checkpoint(cp, sample_interval=interval)
        assert restored.sample_count == 0
        assert restored._interval_low == float("inf")

    def test_version_mismatch(self) -> None:
        cp = {"version": 99, "sample_interval_seconds": 60.0}
        with pytest.raises(ValueError, match="Unsupported checkpoint version"):
            FeatureStore.from_checkpoint(cp, sample_interval=timedelta(seconds=60))

    def test_interval_mismatch(self) -> None:
        store = FeatureStore(sample_interval=timedelta(seconds=60))
        cp = store.to_checkpoint()
        with pytest.raises(ValueError, match="sample_interval mismatch"):
            FeatureStore.from_checkpoint(cp, sample_interval=timedelta(seconds=300))


class TestRestoreStore:
    @pytest.mark.asyncio
    async def test_fallback_on_none(self) -> None:
        """Returns empty store when Redis has no checkpoint."""
        mock_redis: AsyncMock = AsyncMock()
        mock_redis.get.return_value = None
        store = await _restore_store(
            mock_redis, "ETH-PERP", "slow", 500, timedelta(seconds=300),
        )
        assert store.sample_count == 0

    @pytest.mark.asyncio
    async def test_fallback_on_corrupt_data(self) -> None:
        """Returns empty store when Redis payload is invalid JSON."""
        mock_redis: AsyncMock = AsyncMock()
        mock_redis.get.return_value = b"not-json"
        store = await _restore_store(
            mock_redis, "ETH-PERP", "slow", 500, timedelta(seconds=300),
        )
        assert store.sample_count == 0

    @pytest.mark.asyncio
    async def test_restores_valid_checkpoint(self) -> None:
        """Successfully restores from a valid checkpoint payload."""
        interval = timedelta(seconds=300)
        original = FeatureStore(sample_interval=interval)
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            original.update(
                _make_snapshot(price=100.0 + i, ts=base + timedelta(seconds=300 * i))
            )
        payload = orjson.dumps(original.to_checkpoint())

        mock_redis: AsyncMock = AsyncMock()
        mock_redis.get.return_value = payload
        store = await _restore_store(mock_redis, "ETH-PERP", "slow", 500, interval)
        assert store.sample_count == 5
        assert list(store._closes) == list(original._closes)
