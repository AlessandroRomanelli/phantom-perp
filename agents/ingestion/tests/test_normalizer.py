"""Tests for the MarketSnapshot normalizer."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.coinbase.models import CandleResponse

from agents.ingestion.normalizer import (
    _hours_since_last_funding,
    _next_hour,
    build_snapshot,
    snapshot_to_dict,
)
from agents.ingestion.state import BookLevel, IngestionState

TEST_INSTRUMENT_ID = "ETH-PERP"


def _populated_state() -> IngestionState:
    """Create a state with all minimum required fields populated."""
    state = IngestionState(instrument_id=TEST_INSTRUMENT_ID)
    state.best_bid = Decimal("2230.50")
    state.best_ask = Decimal("2231.00")
    state.last_price = Decimal("2230.75")
    state.mark_price = Decimal("2230.60")
    state.index_price = Decimal("2230.55")
    state.volume_24h = Decimal("15000")
    state.open_interest = Decimal("82000")
    state.funding_rate = Decimal("0.0001")
    state.next_funding_time = datetime(2025, 6, 15, 14, 0, 0, tzinfo=UTC)
    state.bid_depth = [
        BookLevel(Decimal("2230.50"), Decimal("10")),
        BookLevel(Decimal("2230.00"), Decimal("20")),
    ]
    state.ask_depth = [
        BookLevel(Decimal("2231.00"), Decimal("15")),
        BookLevel(Decimal("2231.50"), Decimal("25")),
    ]
    return state


class TestBuildSnapshot:
    def test_builds_snapshot_from_full_state(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)

        assert snapshot is not None
        assert snapshot.instrument == TEST_INSTRUMENT_ID
        assert snapshot.mark_price == Decimal("2230.60")
        assert snapshot.index_price == Decimal("2230.55")
        assert snapshot.last_price == Decimal("2230.75")
        assert snapshot.best_bid == Decimal("2230.50")
        assert snapshot.best_ask == Decimal("2231.00")
        assert snapshot.volume_24h == Decimal("15000")
        assert snapshot.open_interest == Decimal("82000")
        assert snapshot.funding_rate == Decimal("0.0001")

    def test_returns_none_for_empty_state(self) -> None:
        state = IngestionState(instrument_id=TEST_INSTRUMENT_ID)
        assert build_snapshot(state) is None

    def test_returns_none_for_partial_state(self) -> None:
        state = IngestionState(instrument_id=TEST_INSTRUMENT_ID)
        state.best_bid = Decimal("2230.50")
        state.best_ask = Decimal("2231.00")
        # Missing last_price, mark_price, index_price
        assert build_snapshot(state) is None

    def test_spread_bps_computed(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)
        assert snapshot is not None
        assert snapshot.spread_bps > 0

    def test_orderbook_imbalance_computed(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)
        assert snapshot is not None
        # Bid vol = 10+20 = 30, Ask vol = 15+25 = 40
        # Imbalance = (30-40)/70 ≈ -0.143
        assert -0.2 < snapshot.orderbook_imbalance < 0.0

    def test_defaults_when_no_funding(self) -> None:
        state = _populated_state()
        state.funding_rate = None
        state.next_funding_time = None
        snapshot = build_snapshot(state)
        assert snapshot is not None
        assert snapshot.funding_rate == Decimal("0")
        assert snapshot.next_funding_time is not None  # Defaults to next hour

    def test_volatility_zero_without_candles(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)
        assert snapshot is not None
        assert snapshot.volatility_1h == 0.0
        assert snapshot.volatility_24h == 0.0

    def test_volatility_with_candles(self) -> None:
        state = _populated_state()
        prices = [2000.0 + i * 0.5 for i in range(60)]
        base_ts = 1735689600  # 2025-01-01T00:00:00Z
        state.candles_by_granularity["ONE_MINUTE"] = [
            CandleResponse(
                start=str(base_ts + i * 60),
                open=str(p),
                high=str(p + 1),
                low=str(p - 1),
                close=str(p),
                volume="100",
            )
            for i, p in enumerate(prices)
        ]
        snapshot = build_snapshot(state)
        assert snapshot is not None
        assert snapshot.volatility_1h > 0.0


class TestSnapshotToDict:
    def test_serializes_all_fields(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)
        assert snapshot is not None
        d = snapshot_to_dict(snapshot)

        assert d["instrument"] == TEST_INSTRUMENT_ID
        assert d["mark_price"] == "2230.60"
        assert d["index_price"] == "2230.55"
        assert d["last_price"] == "2230.75"
        assert d["best_bid"] == "2230.50"
        assert d["best_ask"] == "2231.00"
        assert d["volume_24h"] == "15000"
        assert d["funding_rate"] == "0.0001"
        assert isinstance(d["spread_bps"], float)
        assert isinstance(d["orderbook_imbalance"], float)
        assert isinstance(d["volatility_1h"], float)
        assert isinstance(d["volatility_24h"], float)
        assert isinstance(d["timestamp"], str)
        assert isinstance(d["next_funding_time"], str)

    def test_decimals_serialized_as_strings(self) -> None:
        state = _populated_state()
        snapshot = build_snapshot(state)
        assert snapshot is not None
        d = snapshot_to_dict(snapshot)

        for key in ["mark_price", "index_price", "last_price", "best_bid",
                     "best_ask", "volume_24h", "open_interest", "funding_rate"]:
            assert isinstance(d[key], str), f"{key} should be a string"


class TestHoursSinceLastFunding:
    def test_just_after_settlement(self) -> None:
        # Next funding is at 15:00, current time is 14:01
        now = datetime(2025, 6, 15, 14, 1, 0, tzinfo=UTC)
        next_f = datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)
        hours = _hours_since_last_funding(now, next_f)
        # Last settlement was 14:00, elapsed ~1 minute = ~0.017h
        assert 0.0 < hours < 0.1

    def test_halfway_between_settlements(self) -> None:
        now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=UTC)
        next_f = datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)
        hours = _hours_since_last_funding(now, next_f)
        assert 0.45 < hours < 0.55

    def test_just_before_settlement(self) -> None:
        now = datetime(2025, 6, 15, 14, 59, 0, tzinfo=UTC)
        next_f = datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)
        hours = _hours_since_last_funding(now, next_f)
        assert 0.95 < hours <= 1.0

    def test_clamped_to_one(self) -> None:
        # Edge case: now is past the next funding time
        now = datetime(2025, 6, 15, 15, 30, 0, tzinfo=UTC)
        next_f = datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)
        hours = _hours_since_last_funding(now, next_f)
        assert hours == 1.0


class TestNextHour:
    def test_rounds_up(self) -> None:
        now = datetime(2025, 6, 15, 14, 32, 45, 123456, tzinfo=UTC)
        nh = _next_hour(now)
        assert nh == datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)

    def test_exact_hour(self) -> None:
        now = datetime(2025, 6, 15, 14, 0, 0, 0, tzinfo=UTC)
        nh = _next_hour(now)
        # At exactly 14:00:00.000, next hour is 15:00
        assert nh == datetime(2025, 6, 15, 15, 0, 0, tzinfo=UTC)
