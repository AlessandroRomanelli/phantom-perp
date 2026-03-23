"""Tests for signal agent serialization helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal

from agents.signals.feature_store import FeatureStore
from agents.signals.main import deserialize_snapshot, signal_to_dict
from agents.signals.tests.test_feature_store import _snap

ALL_INSTRUMENTS = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]

TEST_INSTRUMENT_ID = "ETH-PERP"


class TestDeserializeSnapshot:
    def test_roundtrip(self) -> None:
        payload = {
            "timestamp": "2025-06-15T12:00:00+00:00",
            "instrument": TEST_INSTRUMENT_ID,
            "mark_price": "2230.60",
            "index_price": "2230.10",
            "last_price": "2230.50",
            "best_bid": "2230.25",
            "best_ask": "2230.75",
            "spread_bps": 2.24,
            "volume_24h": "15000",
            "open_interest": "80000",
            "funding_rate": "0.0001",
            "next_funding_time": "2025-06-15T13:00:00+00:00",
            "hours_since_last_funding": 0.5,
            "orderbook_imbalance": -0.1,
            "volatility_1h": 0.15,
            "volatility_24h": 0.45,
        }

        snap = deserialize_snapshot(payload)

        assert snap.instrument == TEST_INSTRUMENT_ID
        assert snap.mark_price == Decimal("2230.60")
        assert snap.best_bid == Decimal("2230.25")
        assert snap.spread_bps == 2.24
        assert snap.funding_rate == Decimal("0.0001")


class TestSignalToDict:
    def test_serializes_all_fields(self) -> None:
        signal = StandardSignal(
            signal_id="sig-test-123",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            instrument=TEST_INSTRUMENT_ID,
            direction=PositionSide.LONG,
            conviction=0.75,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=4),
            reasoning="Test signal",
            suggested_target=PortfolioTarget.B,
            entry_price=Decimal("2230.00"),
            stop_loss=Decimal("2200.00"),
            take_profit=Decimal("2280.00"),
            metadata={"fast_ema": 2231.5},
        )

        d = signal_to_dict(signal)

        assert d["signal_id"] == "sig-test-123"
        assert d["direction"] == "LONG"
        assert d["conviction"] == 0.75
        assert d["source"] == "momentum"
        assert d["time_horizon_seconds"] == 14400
        assert d["suggested_target"] == "user_confirmed"
        assert d["entry_price"] == "2230.00"
        assert d["stop_loss"] == "2200.00"
        assert d["take_profit"] == "2280.00"
        assert d["metadata"]["fast_ema"] == 2231.5

    def test_none_optional_fields(self) -> None:
        signal = StandardSignal(
            signal_id="sig-test-456",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            instrument=TEST_INSTRUMENT_ID,
            direction=PositionSide.SHORT,
            conviction=0.5,
            source=SignalSource.MEAN_REVERSION,
            time_horizon=timedelta(hours=2),
            reasoning="Test",
        )

        d = signal_to_dict(signal)
        assert d["suggested_target"] is None
        assert d["entry_price"] is None
        assert d["stop_loss"] is None
        assert d["take_profit"] is None


class TestMultiInstrumentRouting:
    """Verify FeatureStore routing mirrors signals agent per-instrument dispatch."""

    def _make_stores(self) -> dict[str, FeatureStore]:
        return {
            iid: FeatureStore(sample_interval=timedelta(seconds=0))
            for iid in ALL_INSTRUMENTS
        }

    def test_all_instruments_route_to_correct_store(self) -> None:
        stores = self._make_stores()
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        for iid in ALL_INSTRUMENTS:
            snapshot = _snap(ts, instrument=iid)
            store = stores.get(snapshot.instrument)
            assert store is not None
            store.update(snapshot)

        for iid in ALL_INSTRUMENTS:
            assert stores[iid].sample_count == 1, f"{iid} should have 1 sample"

    def test_snapshot_only_updates_matching_store(self) -> None:
        stores = self._make_stores()
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        snapshot = _snap(ts, instrument="ETH-PERP")
        store = stores.get(snapshot.instrument)
        assert store is not None
        store.update(snapshot)

        assert stores["ETH-PERP"].sample_count == 1
        for iid in ALL_INSTRUMENTS:
            if iid != "ETH-PERP":
                assert stores[iid].sample_count == 0, f"{iid} should be empty"

    def test_unknown_instrument_skipped(self) -> None:
        stores = self._make_stores()
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        snapshot = _snap(ts, instrument="UNKNOWN-PERP")
        store = stores.get(snapshot.instrument)
        assert store is None

        for iid in ALL_INSTRUMENTS:
            assert stores[iid].sample_count == 0

    def test_multiple_snapshots_accumulate_per_instrument(self) -> None:
        stores = self._make_stores()
        base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        for i in range(3):
            ts = base + timedelta(seconds=i)
            for iid in ALL_INSTRUMENTS:
                snapshot = _snap(ts, mark=2230.0 + i, instrument=iid)
                store = stores.get(snapshot.instrument)
                assert store is not None
                store.update(snapshot)

        for iid in ALL_INSTRUMENTS:
            assert stores[iid].sample_count == 3, f"{iid} should have 3 samples"
