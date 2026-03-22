"""Tests for multi-instrument REST polling wiring in main.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.ingestion.main import _mark_stale_rest_data, _run_rest_poller_isolated
from agents.ingestion.state import IngestionState
from libs.common.constants import REST_CANDLE_STALE_SECONDS, REST_FUNDING_STALE_SECONDS


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
