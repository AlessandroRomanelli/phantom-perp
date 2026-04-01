"""Tests for historical candle warmup of FeatureStores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.signals.feature_store import FeatureStore
from agents.signals.warmup import (
    _candle_to_snapshot,
    _fetch_candles,
    _pick_granularity,
    warmup_all_stores,
    warmup_feature_store,
)
from libs.coinbase.models import CandleResponse

# ---------------------------------------------------------------------------
# _pick_granularity
# ---------------------------------------------------------------------------


class TestPickGranularity:
    """Tests for granularity selection based on sample interval."""

    def test_30s_interval_picks_one_minute(self) -> None:
        gran, dur = _pick_granularity(30)
        assert gran == "ONE_MINUTE"
        assert dur == 60

    def test_60s_interval_picks_one_minute(self) -> None:
        gran, dur = _pick_granularity(60)
        assert gran == "ONE_MINUTE"
        assert dur == 60

    def test_300s_interval_picks_five_minute(self) -> None:
        gran, dur = _pick_granularity(300)
        assert gran == "FIVE_MINUTE"
        assert dur == 300

    def test_3600s_interval_picks_one_hour(self) -> None:
        gran, dur = _pick_granularity(3600)
        assert gran == "ONE_HOUR"
        assert dur == 3600


# ---------------------------------------------------------------------------
# _candle_to_snapshot
# ---------------------------------------------------------------------------


class TestCandleToSnapshot:
    """Tests for converting CandleResponse to MarketSnapshot."""

    def test_basic_conversion(self) -> None:
        candle = CandleResponse(
            start="1700000000",
            low="1800.00",
            high="1850.00",
            open="1810.00",
            close="1840.00",
            volume="1234.56",
        )
        snap = _candle_to_snapshot(candle, "ETH-PERP")

        assert snap.instrument == "ETH-PERP"
        assert snap.last_price == Decimal("1840.00")
        assert snap.mark_price == Decimal("1840.00")
        assert snap.volume_24h == Decimal("1234.56")
        assert snap.timestamp == datetime.fromtimestamp(1700000000, tz=UTC)
        # OI is NaN sentinel, funding is zero (skipped by FeatureStore)
        assert snap.open_interest.is_nan()
        assert snap.funding_rate == Decimal("0")
        assert snap.orderbook_imbalance != snap.orderbook_imbalance  # NaN != NaN

    def test_preserves_instrument_id(self) -> None:
        candle = CandleResponse(
            start="1700000000", low="100", high="200",
            open="150", close="175", volume="10",
        )
        snap = _candle_to_snapshot(candle, "BTC-PERP")
        assert snap.instrument == "BTC-PERP"


# ---------------------------------------------------------------------------
# _fetch_candles
# ---------------------------------------------------------------------------


class TestFetchCandles:
    """Tests for candle fetching with pagination."""

    @pytest.mark.asyncio
    async def test_single_batch(self) -> None:
        """Fetches candles in a single request when count <= 300."""
        mock_client = AsyncMock()
        candles = [
            CandleResponse(
                start=str(1700000000 + i * 300),
                low="100", high="200", open="150", close="175", volume="10",
            )
            for i in range(50)
        ]
        mock_client.get_candles.return_value = candles

        result = await _fetch_candles(mock_client, "ETH-PERP-INTX", "FIVE_MINUTE", 300, 50)
        assert len(result) == 50
        mock_client.get_candles.assert_called_once()

    @pytest.mark.asyncio
    async def test_pagination_multiple_batches(self) -> None:
        """Fetches candles across multiple requests for count > 300."""
        mock_client = AsyncMock()

        # First call returns 300 candles, second returns 100
        batch1 = [
            CandleResponse(
                start=str(1700000000 + i * 60),
                low="100", high="200", open="150", close="175", volume="10",
            )
            for i in range(300)
        ]
        batch2 = [
            CandleResponse(
                start=str(1700000000 - (300 - i) * 60),
                low="100", high="200", open="150", close="175", volume="10",
            )
            for i in range(100)
        ]
        mock_client.get_candles.side_effect = [batch1, batch2]

        result = await _fetch_candles(mock_client, "ETH-PERP-INTX", "ONE_MINUTE", 60, 400)
        assert len(result) == 400
        assert mock_client.get_candles.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        """Returns empty list when API returns no candles."""
        mock_client = AsyncMock()
        mock_client.get_candles.return_value = []

        result = await _fetch_candles(mock_client, "ETH-PERP-INTX", "FIVE_MINUTE", 300, 100)
        assert result == []

    @pytest.mark.asyncio
    async def test_partial_response_stops(self) -> None:
        """Stops fetching when API returns fewer candles than requested."""
        mock_client = AsyncMock()
        candles = [
            CandleResponse(
                start=str(1700000000 + i * 300),
                low="100", high="200", open="150", close="175", volume="10",
            )
            for i in range(50)
        ]
        mock_client.get_candles.return_value = candles

        result = await _fetch_candles(mock_client, "ETH-PERP-INTX", "FIVE_MINUTE", 300, 300)
        assert len(result) == 50
        # Should only call once since 50 < 300 (batch_size)
        mock_client.get_candles.assert_called_once()


# ---------------------------------------------------------------------------
# warmup_feature_store
# ---------------------------------------------------------------------------


def _make_candles(count: int, granularity_secs: int = 300) -> list[CandleResponse]:
    """Build a list of ascending-time candles for testing."""
    base_ts = 1700000000
    return [
        CandleResponse(
            start=str(base_ts + i * granularity_secs),
            low=str(1800 + i),
            high=str(1850 + i),
            open=str(1810 + i),
            close=str(1840 + i),
            volume=str(100 + i),
        )
        for i in range(count)
    ]


class TestWarmupFeatureStore:
    """Tests for warming up a single FeatureStore."""

    @pytest.mark.asyncio
    async def test_populates_empty_store(self) -> None:
        """Fills an empty store with candle data."""
        store = FeatureStore(max_samples=100, sample_interval=timedelta(seconds=300))
        assert store.sample_count == 0

        mock_client = AsyncMock()
        candles = _make_candles(100, 300)
        mock_client.get_candles.return_value = candles

        # Mock instrument registry
        mock_inst = MagicMock()
        mock_inst.product_id = "ETH-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            added = await warmup_feature_store(mock_client, store, "ETH-PERP")
            assert added > 0
            assert store.sample_count == added
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_skips_when_store_nearly_full(self) -> None:
        """Skips warmup when store is >= 80% full."""
        store = FeatureStore(max_samples=100, sample_interval=timedelta(seconds=300))
        # Pre-fill with 85 samples
        base_ts = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(85):
            store._last_sample_time = base_ts + timedelta(seconds=300 * i) - timedelta(seconds=301)
            snap = _candle_to_snapshot(
                CandleResponse(
                    start=str(int((base_ts + timedelta(seconds=300 * i)).timestamp())),
                    low="1800", high="1850", open="1810", close="1840", volume="100",
                ),
                "ETH-PERP",
            )
            store.update(snap)
        assert store.sample_count >= 80

        mock_client = AsyncMock()
        added = await warmup_feature_store(mock_client, store, "ETH-PERP")
        assert added == 0
        mock_client.get_candles.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_api_failure_gracefully(self) -> None:
        """Returns 0 and logs when candle fetch fails."""
        store = FeatureStore(max_samples=100, sample_interval=timedelta(seconds=300))
        mock_client = AsyncMock()
        mock_client.get_candles.side_effect = RuntimeError("Connection refused")

        mock_inst = MagicMock()
        mock_inst.product_id = "ETH-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            added = await warmup_feature_store(mock_client, store, "ETH-PERP")
            assert added == 0
            assert store.sample_count == 0
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_chronological_order_preserved(self) -> None:
        """Verifies samples are added in chronological order."""
        store = FeatureStore(max_samples=50, sample_interval=timedelta(seconds=300))

        mock_client = AsyncMock()
        candles = _make_candles(30, 300)
        mock_client.get_candles.return_value = candles

        mock_inst = MagicMock()
        mock_inst.product_id = "ETH-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            await warmup_feature_store(mock_client, store, "ETH-PERP")
            # Timestamps should be monotonically increasing
            ts = list(store._timestamps)
            for i in range(1, len(ts)):
                assert ts[i] > ts[i - 1], f"Timestamps not ascending at index {i}"
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# warmup_all_stores
# ---------------------------------------------------------------------------


def _mock_funding_response() -> None:
    """No longer needed — warmup doesn't fetch live baseline."""


class TestWarmupAllStores:
    """Tests for warming up all stores across instruments."""

    @pytest.mark.asyncio
    async def test_warms_both_slow_and_fast(self) -> None:
        """Populates both slow and fast stores for each instrument."""
        slow = {"ETH-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=300))}
        fast = {"ETH-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=30))}

        mock_client = AsyncMock()
        mock_client.get_candles.return_value = _make_candles(50, 300)

        mock_inst = MagicMock()
        mock_inst.product_id = "ETH-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            results = await warmup_all_stores(mock_client, slow, fast)
            assert "ETH-PERP" in results
            assert results["ETH-PERP"]["slow"] > 0
            assert results["ETH-PERP"]["fast"] > 0
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_handles_multiple_instruments(self) -> None:
        """Processes all instruments."""
        slow = {
            "ETH-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=300)),
            "BTC-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=300)),
        }
        fast = {
            "ETH-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=30)),
            "BTC-PERP": FeatureStore(max_samples=50, sample_interval=timedelta(seconds=30)),
        }

        mock_client = AsyncMock()
        mock_client.get_candles.return_value = _make_candles(50, 300)

        mock_inst = MagicMock()
        mock_inst.product_id = "MOCK-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            results = await warmup_all_stores(mock_client, slow, fast)
            assert "ETH-PERP" in results
            assert "BTC-PERP" in results
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_nan_filtered_from_oi_and_funding(self) -> None:
        """Warmup samples write NaN for OI; FeatureStore filters them out."""
        slow = {"BTC-PERP": FeatureStore(max_samples=20, sample_interval=timedelta(seconds=300))}
        fast: dict[str, FeatureStore] = {}

        mock_client = AsyncMock()
        mock_client.get_candles.return_value = _make_candles(20, 300)

        mock_inst = MagicMock()
        mock_inst.product_id = "BTC-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            await warmup_all_stores(mock_client, slow, fast)
            store = slow["BTC-PERP"]
            # Price data should be fully populated
            assert store.sample_count == 20
            # OI property should return empty — all warmup NaN values filtered
            assert len(store.open_interests) == 0
            # Funding deque should be empty — funding_rate=0 is skipped by update()
            assert store.funding_rate_count == 0
            # Orderbook imbalance also filtered
            assert len(store.orderbook_imbalances) == 0
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_live_data_visible_after_warmup(self) -> None:
        """After warmup, live snapshots with real OI appear in the property."""
        store = FeatureStore(max_samples=10, sample_interval=timedelta(seconds=300))

        mock_client = AsyncMock()
        mock_client.get_candles.return_value = _make_candles(5, 300)

        mock_inst = MagicMock()
        mock_inst.product_id = "ETH-PERP-INTX"
        import agents.signals.warmup as warmup_mod
        original_get = warmup_mod.get_instrument
        warmup_mod.get_instrument = lambda _: mock_inst  # type: ignore[assignment]

        try:
            await warmup_feature_store(mock_client, store, "ETH-PERP")
            assert store.sample_count == 5
            assert len(store.open_interests) == 0  # All NaN

            # Simulate a live snapshot with real OI
            from libs.common.models.market_snapshot import MarketSnapshot
            live_snap = MarketSnapshot(
                timestamp=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
                instrument="ETH-PERP",
                mark_price=Decimal("2100"),
                index_price=Decimal("2100"),
                last_price=Decimal("2100"),
                best_bid=Decimal("2099"),
                best_ask=Decimal("2101"),
                spread_bps=0.5,
                volume_24h=Decimal("1000"),
                open_interest=Decimal("5000"),
                funding_rate=Decimal("0.0001"),
                next_funding_time=datetime(2024, 6, 1, 13, 0, tzinfo=UTC),
                hours_since_last_funding=0.5,
                orderbook_imbalance=0.15,
                volatility_1h=0.02,
                volatility_24h=0.05,
            )
            store._last_sample_time = live_snap.timestamp - store._sample_interval - timedelta(seconds=1)
            store.update(live_snap)

            # Now OI should have exactly 1 valid value
            assert len(store.open_interests) == 1
            assert store.open_interests[0] == 5000.0
            assert store.funding_rate_count == 1
        finally:
            warmup_mod.get_instrument = original_get  # type: ignore[assignment]
