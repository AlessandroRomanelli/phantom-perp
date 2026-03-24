"""Tests for multi-instrument REST polling wiring in main.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agents.ingestion.main import _mark_stale_rest_data, _run_rest_poller_isolated
from agents.ingestion.normalizer import build_snapshot
from agents.ingestion.state import IngestionState
from libs.common.constants import REST_CANDLE_STALE_SECONDS, REST_FUNDING_STALE_SECONDS

ALL_INSTRUMENTS = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]


def _ready_state(instrument_id: str, mark_price: str = "100.00") -> IngestionState:
    """Create an IngestionState that passes is_ready() and has_minimum_data()."""
    state = IngestionState(instrument_id=instrument_id)
    state.has_ws_tick = True
    state.has_candles = True
    state.has_funding = True
    price = Decimal(mark_price)
    state.best_bid = price - Decimal("0.25")
    state.best_ask = price + Decimal("0.25")
    state.last_price = price
    state.mark_price = price
    state.index_price = price - Decimal("0.10")
    return state


class TestMarkStaleRestData:
    """Tests for _mark_stale_rest_data staleness detection."""

    def test_resets_has_candles_when_stale(self) -> None:
        now = datetime.now(UTC)
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_candles = True
        state.last_candle_update = now - timedelta(seconds=REST_CANDLE_STALE_SECONDS + 60)
        states = {"ETH-PERP": state}

        _mark_stale_rest_data(states)

        assert state.has_candles is False

    def test_resets_has_funding_when_stale(self) -> None:
        now = datetime.now(UTC)
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_funding = True
        state.last_funding_update = now - timedelta(seconds=REST_FUNDING_STALE_SECONDS + 60)
        states = {"ETH-PERP": state}

        _mark_stale_rest_data(states)

        assert state.has_funding is False

    def test_no_reset_when_fresh(self) -> None:
        now = datetime.now(UTC)
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_candles = True
        state.last_candle_update = now - timedelta(seconds=10)
        state.has_funding = True
        state.last_funding_update = now - timedelta(seconds=10)
        states = {"ETH-PERP": state}

        _mark_stale_rest_data(states)

        assert state.has_candles is True
        assert state.has_funding is True

    def test_multiple_instruments_independent(self) -> None:
        now = datetime.now(UTC)
        stale = IngestionState(instrument_id="ETH-PERP")
        stale.has_candles = True
        stale.last_candle_update = now - timedelta(seconds=REST_CANDLE_STALE_SECONDS + 60)

        fresh = IngestionState(instrument_id="BTC-PERP")
        fresh.has_candles = True
        fresh.last_candle_update = now - timedelta(seconds=10)

        states = {"ETH-PERP": stale, "BTC-PERP": fresh}

        _mark_stale_rest_data(states)

        assert stale.has_candles is False
        assert fresh.has_candles is True


class TestRunRestPollerIsolated:
    """Tests for _run_rest_poller_isolated error isolation."""

    @pytest.mark.asyncio
    async def test_catches_exception_without_propagation(self) -> None:
        async def failing_coro() -> None:
            raise RuntimeError("simulated crash")

        # Should NOT raise
        await _run_rest_poller_isolated(failing_coro(), "ETH-PERP", "candle_poller")

    @pytest.mark.asyncio
    async def test_allows_normal_completion(self) -> None:
        completed = False

        async def ok_coro() -> None:
            nonlocal completed
            completed = True

        await _run_rest_poller_isolated(ok_coro(), "ETH-PERP", "funding_poller")
        assert completed is True


class TestMultiInstrumentE2E:
    """E2E tests verifying all 5 instruments produce correct MarketSnapshots."""

    def test_all_instruments_produce_snapshots(self) -> None:
        """All 5 instruments produce non-None MarketSnapshots with correct instrument field."""
        for instrument_id in ALL_INSTRUMENTS:
            state = _ready_state(instrument_id)
            snapshot = build_snapshot(state)
            assert snapshot is not None, f"{instrument_id} returned None snapshot"
            assert snapshot.instrument == instrument_id, (
                f"Expected instrument={instrument_id}, got {snapshot.instrument}"
            )

    def test_snapshot_instrument_field_matches_state(self) -> None:
        """Each instrument gets its own mark_price and correct instrument field."""
        prices = {
            "ETH-PERP": "2230.50",
            "BTC-PERP": "65000.00",
            "SOL-PERP": "150.00",
            "QQQ-PERP": "480.00",
            "SPY-PERP": "550.00",
        }
        for instrument_id, price in prices.items():
            state = _ready_state(instrument_id, mark_price=price)
            snapshot = build_snapshot(state)
            assert snapshot is not None
            assert snapshot.instrument == instrument_id
            assert snapshot.mark_price == Decimal(price), (
                f"{instrument_id}: expected mark_price={price}, got {snapshot.mark_price}"
            )

    def test_build_snapshot_with_cross_check_passes(self) -> None:
        """Cross-check with matching instrument_id raises no error."""
        for instrument_id in ALL_INSTRUMENTS:
            state = _ready_state(instrument_id)
            snapshot = build_snapshot(state, instrument_id=state.instrument_id)
            assert snapshot is not None
            assert snapshot.instrument == instrument_id

    def test_build_snapshot_with_wrong_instrument_raises(self) -> None:
        """Cross-check with mismatched instrument_id raises AssertionError."""
        state = _ready_state("ETH-PERP")
        with pytest.raises(AssertionError, match="mismatch"):
            build_snapshot(state, instrument_id="BTC-PERP")

    def test_concurrent_instruments_no_cross_contamination(self) -> None:
        """5 states with distinct prices produce 5 snapshots with no cross-contamination."""
        prices = {
            "ETH-PERP": "2230.50",
            "BTC-PERP": "65000.00",
            "SOL-PERP": "150.00",
            "QQQ-PERP": "480.00",
            "SPY-PERP": "550.00",
        }
        states = {iid: _ready_state(iid, mark_price=p) for iid, p in prices.items()}

        for instrument_id, state in states.items():
            snapshot = build_snapshot(state, instrument_id=instrument_id)
            assert snapshot is not None
            assert snapshot.instrument == instrument_id
            assert snapshot.mark_price == Decimal(prices[instrument_id]), (
                f"{instrument_id}: cross-contamination detected, "
                f"expected {prices[instrument_id]}, got {snapshot.mark_price}"
            )
