"""Tests for the candle poller."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from libs.coinbase.models import CandleResponse
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError

from agents.ingestion.sources.candles import (
    TIMEFRAMES,
    TimeframeConfig,
    poll_candles_once,
)
from agents.ingestion.state import IngestionState


@pytest.fixture
def state() -> IngestionState:
    return IngestionState(instrument_id="ETH-PERP")


@pytest.fixture
def tf() -> TimeframeConfig:
    return TimeframeConfig(
        granularity="ONE_HOUR",
        poll_interval_seconds=600,
        max_candles=48,
    )


def _make_candle_response(close: str = "2230.00") -> CandleResponse:
    from datetime import UTC, datetime
    return CandleResponse(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        open=Decimal("2225.00"),
        high=Decimal("2240.00"),
        low=Decimal("2220.00"),
        close=Decimal(close),
        volume=Decimal("100"),
    )


class TestPollCandlesOnce:
    @pytest.mark.asyncio
    async def test_stores_candles_in_state(self, state: IngestionState, tf: TimeframeConfig) -> None:
        mock_client = AsyncMock()
        candles = [_make_candle_response(str(2230 + i)) for i in range(10)]
        mock_client.get_candles.return_value = candles

        await poll_candles_once(mock_client, state, tf)

        assert "ONE_HOUR" in state.candles_by_granularity
        assert len(state.candles_by_granularity["ONE_HOUR"]) == 10
        call_kwargs = mock_client.get_candles.call_args.kwargs
        assert call_kwargs["instrument_id"] == "ETH-PERP"
        assert call_kwargs["granularity"] == "ONE_HOUR"
        assert "start" in call_kwargs

    @pytest.mark.asyncio
    async def test_truncates_to_max_candles(self, state: IngestionState) -> None:
        tf = TimeframeConfig(granularity="ONE_MINUTE", poll_interval_seconds=60, max_candles=5)
        mock_client = AsyncMock()
        candles = [_make_candle_response(str(2230 + i)) for i in range(20)]
        mock_client.get_candles.return_value = candles

        await poll_candles_once(mock_client, state, tf)

        assert len(state.candles_by_granularity["ONE_MINUTE"]) == 5

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self, state: IngestionState, tf: TimeframeConfig) -> None:
        mock_client = AsyncMock()
        mock_client.get_candles.side_effect = RateLimitExceededError(
            endpoint="/candles", retry_after=1.0
        )

        # Should not raise
        await poll_candles_once(mock_client, state, tf)
        assert "ONE_HOUR" not in state.candles_by_granularity

    @pytest.mark.asyncio
    async def test_handles_api_error(self, state: IngestionState, tf: TimeframeConfig) -> None:
        mock_client = AsyncMock()
        mock_client.get_candles.side_effect = CoinbaseAPIError(500, "Internal", "/candles")

        # Should not raise
        await poll_candles_once(mock_client, state, tf)
        assert "ONE_HOUR" not in state.candles_by_granularity


    @pytest.mark.asyncio
    async def test_poll_candles_once_sets_last_candle_update(
        self, state: IngestionState, tf: TimeframeConfig
    ) -> None:
        mock_client = AsyncMock()
        candles = [_make_candle_response(str(2230 + i)) for i in range(5)]
        mock_client.get_candles.return_value = candles

        assert state.last_candle_update is None
        await poll_candles_once(mock_client, state, tf, instrument_id="BTC-PERP")

        assert state.last_candle_update is not None
        call_kwargs = mock_client.get_candles.call_args.kwargs
        assert call_kwargs["instrument_id"] == "BTC-PERP"


class TestTimeframeConfig:
    def test_all_timeframes_have_positive_intervals(self) -> None:
        for tf in TIMEFRAMES:
            assert tf.poll_interval_seconds > 0
            assert tf.max_candles > 0

    def test_faster_timeframes_poll_more_frequently(self) -> None:
        intervals = [tf.poll_interval_seconds for tf in TIMEFRAMES]
        # Should be non-decreasing (faster timeframes poll more often)
        assert intervals == sorted(intervals)
