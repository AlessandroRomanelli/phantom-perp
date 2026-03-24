"""Tests for the funding rate poller."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from libs.coinbase.models import FundingRateResponse
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError

from agents.ingestion.sources.funding_rate import poll_funding_once
from agents.ingestion.state import IngestionState


@pytest.fixture
def state() -> IngestionState:
    return IngestionState(instrument_id="ETH-PERP")


def _make_funding_response() -> FundingRateResponse:
    return FundingRateResponse(
        product_id="ETH-PERP-INTX",
        funding_rate=Decimal("0.0001"),
        mark_price=Decimal("2230.60"),
    )


class TestPollFundingOnce:
    @pytest.mark.asyncio
    async def test_updates_state(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.return_value = _make_funding_response()

        await poll_funding_once(mock_client, state)

        assert state.funding_rate == Decimal("0.0001")
        assert state.next_funding_time is None  # Advanced Trade has no event_time
        assert state.funding_mark_price == Decimal("2230.60")
        assert state.last_funding_update is not None

    @pytest.mark.asyncio
    async def test_publishes_to_stream(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.return_value = _make_funding_response()
        mock_publisher = AsyncMock()
        mock_publisher.publish.return_value = "1234-0"

        await poll_funding_once(mock_client, state, publisher=mock_publisher)

        mock_publisher.publish.assert_called_once()
        call_args = mock_publisher.publish.call_args
        channel = call_args[0][0]
        payload = call_args[0][1]

        assert channel == "stream:funding_updates"
        assert payload["instrument"] == "ETH-PERP"
        assert payload["rate"] == "0.0001"

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.side_effect = RateLimitExceededError(
            endpoint="/funding", retry_after=1.0
        )

        await poll_funding_once(mock_client, state)
        assert state.funding_rate is None

    @pytest.mark.asyncio
    async def test_handles_api_error(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.side_effect = CoinbaseAPIError(
            500, "Internal", "/funding"
        )

        await poll_funding_once(mock_client, state)
        assert state.funding_rate is None

    @pytest.mark.asyncio
    async def test_forwards_instrument_id(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.return_value = _make_funding_response()

        await poll_funding_once(mock_client, state, instrument_id="BTC-PERP")

        call_kwargs = mock_client.get_funding_rate.call_args.kwargs
        assert call_kwargs["product_id"] == "BTC-PERP"
        assert state.last_funding_update is not None

    @pytest.mark.asyncio
    async def test_no_publish_without_publisher(self, state: IngestionState) -> None:
        mock_client = AsyncMock()
        mock_client.get_funding_rate.return_value = _make_funding_response()

        # No publisher passed — should work fine
        await poll_funding_once(mock_client, state, publisher=None)
        assert state.funding_rate == Decimal("0.0001")
