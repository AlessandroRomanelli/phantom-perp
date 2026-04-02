"""Tests for live order routing in the execution agent.

Covers:
  - TestPlaceOrderLive: LIMIT, MARKET, STOP_LIMIT, STOP_MARKET routing via pool
  - TestCancelOrderLive: routes to correct portfolio client, paper no-op
  - TestErrorHandling: rejected orders, rate limits, unknown instruments
  - TestPaperModeRegression: paper path unchanged after signature additions
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.coinbase.models import OrderResponse
from libs.common.models.enums import OrderSide, OrderType, Route
from libs.common.utils import utc_now

from agents.execution.main import (
    PaperBroker,
    _cancel_order,
    _place_order,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order_response(
    order_id: str = "live-order-1",
    product_id: str = "ETH-PERP-INTX",
    status: str = "FILLED",
) -> OrderResponse:
    return OrderResponse(
        order_id=order_id,
        client_order_id="client-1",
        product_id=product_id,
        side="BUY",
        order_type="LIMIT",
        status=status,
        base_size="1.0",
        limit_price="2200.00",
        filled_size="1.0",
        filled_value="2200.00",
        average_filled_price="2200.00",
        total_fees="0.28",
        created_time=utc_now().isoformat(),
    )


def _make_mock_pool(
    route: Route = Route.A,
    response: OrderResponse | None = None,
) -> MagicMock:
    """Build a mock CoinbaseClientPool with an AsyncMock REST client."""
    if response is None:
        response = _make_order_response()

    mock_client = MagicMock()
    mock_client.create_order = AsyncMock(return_value=response)
    mock_client.cancel_order = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.get_client = MagicMock(return_value=mock_client)
    return mock_pool


# ---------------------------------------------------------------------------
# Instrument registry helpers — patch get_instrument for unit tests
# ---------------------------------------------------------------------------

_INSTRUMENT_PRODUCT_MAP = {
    "ETH-PERP": "ETH-PERP-INTX",
    "BTC-PERP": "BTC-PERP-INTX",
    "SOL-PERP": "SOL-PERP-INTX",
}


class _FakeInstrumentConfig:
    def __init__(self, product_id: str) -> None:
        self._product_id = product_id

    @property
    def product_id(self) -> str:
        return self._product_id


def _get_instrument_side_effect(instrument_id: str) -> _FakeInstrumentConfig:
    if instrument_id not in _INSTRUMENT_PRODUCT_MAP:
        raise KeyError(f"Unknown instrument: {instrument_id}")
    return _FakeInstrumentConfig(_INSTRUMENT_PRODUCT_MAP[instrument_id])


# ---------------------------------------------------------------------------
# TestPlaceOrderLive
# ---------------------------------------------------------------------------


class TestPlaceOrderLive:
    """_place_order() live branch routes to the correct portfolio client."""

    @pytest.mark.asyncio
    async def test_limit_order_routes_to_route_a(self) -> None:
        """LIMIT order for Portfolio A calls pool.get_client(A).create_order()."""
        mock_pool = _make_mock_pool(Route.A)
        expected_response = _make_order_response(order_id="live-limit-a")
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            result = await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.A,
                instrument="ETH-PERP",
                side="BUY",
                size=Decimal("1.0"),
                order_type="LIMIT",
                limit_price=Decimal("2200.00"),
                stop_price=None,
                client_order_id="test-limit-a",
                reduce_only=False,
                last_price=Decimal("2205.00"),
                client_pool=mock_pool,
            )

        assert result.order_id == "live-limit-a"
        mock_pool.get_client.assert_called_once_with(Route.A)
        mock_pool.get_client.return_value.create_order.assert_called_once_with(
            product_id="ETH-PERP-INTX",
            side="BUY",
            size=Decimal("1.0"),
            order_type="LIMIT",
            limit_price=Decimal("2200.00"),
            stop_price=None,
            client_order_id="test-limit-a",
            reduce_only=False,
            leverage=None,
        )

    @pytest.mark.asyncio
    async def test_limit_order_routes_to_route_b(self) -> None:
        """LIMIT order for Portfolio B calls pool.get_client(B).create_order()."""
        mock_pool = _make_mock_pool(Route.B)
        expected_response = _make_order_response(order_id="live-limit-b")
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            result = await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.B,
                instrument="BTC-PERP",
                side="SELL",
                size=Decimal("0.1"),
                order_type="LIMIT",
                limit_price=Decimal("65000.00"),
                stop_price=None,
                client_order_id="test-limit-b",
                reduce_only=False,
                last_price=Decimal("65100.00"),
                client_pool=mock_pool,
            )

        assert result.order_id == "live-limit-b"
        mock_pool.get_client.assert_called_once_with(Route.B)
        mock_pool.get_client.return_value.create_order.assert_called_once_with(
            product_id="BTC-PERP-INTX",
            side="SELL",
            size=Decimal("0.1"),
            order_type="LIMIT",
            limit_price=Decimal("65000.00"),
            stop_price=None,
            client_order_id="test-limit-b",
            reduce_only=False,
            leverage=None,
        )

    @pytest.mark.asyncio
    async def test_market_order_routes_correctly(self) -> None:
        """MARKET order passes correct order_type to create_order()."""
        mock_pool = _make_mock_pool()
        expected_response = _make_order_response(order_id="live-market")
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            result = await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.A,
                instrument="ETH-PERP",
                side="BUY",
                size=Decimal("2.0"),
                order_type="MARKET",
                limit_price=None,
                stop_price=None,
                client_order_id="test-market",
                reduce_only=False,
                last_price=Decimal("2210.00"),
                client_pool=mock_pool,
            )

        assert result.order_id == "live-market"
        call_kwargs = mock_pool.get_client.return_value.create_order.call_args.kwargs
        assert call_kwargs["order_type"] == "MARKET"
        assert call_kwargs["limit_price"] is None

    @pytest.mark.asyncio
    async def test_stop_limit_order_passes_stop_price(self) -> None:
        """STOP_LIMIT order passes stop_price through to create_order()."""
        mock_pool = _make_mock_pool()
        expected_response = _make_order_response(
            order_id="live-stop-limit", status="OPEN"
        )
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            result = await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.A,
                instrument="SOL-PERP",
                side="SELL",
                size=Decimal("10.0"),
                order_type="STOP_LIMIT",
                limit_price=Decimal("145.00"),
                stop_price=Decimal("148.00"),
                client_order_id="test-stop-limit",
                reduce_only=True,
                last_price=Decimal("150.00"),
                client_pool=mock_pool,
            )

        assert result.order_id == "live-stop-limit"
        call_kwargs = mock_pool.get_client.return_value.create_order.call_args.kwargs
        assert call_kwargs["order_type"] == "STOP_LIMIT"
        assert call_kwargs["stop_price"] == Decimal("148.00")
        assert call_kwargs["limit_price"] == Decimal("145.00")
        assert call_kwargs["reduce_only"] is True

    @pytest.mark.asyncio
    async def test_stop_market_order_passes_through(self) -> None:
        """STOP_MARKET order type is forwarded; rest_client maps it internally."""
        mock_pool = _make_mock_pool()
        expected_response = _make_order_response(
            order_id="live-stop-market", status="OPEN"
        )
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            result = await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.A,
                instrument="ETH-PERP",
                side="SELL",
                size=Decimal("1.5"),
                order_type="STOP_MARKET",
                limit_price=None,
                stop_price=Decimal("2100.00"),
                client_order_id="test-stop-market",
                reduce_only=True,
                last_price=Decimal("2150.00"),
                client_pool=mock_pool,
            )

        assert result.order_id == "live-stop-market"
        call_kwargs = mock_pool.get_client.return_value.create_order.call_args.kwargs
        assert call_kwargs["order_type"] == "STOP_MARKET"
        assert call_kwargs["stop_price"] == Decimal("2100.00")

    @pytest.mark.asyncio
    async def test_instrument_id_resolved_to_product_id(self) -> None:
        """Instrument ID (e.g. ETH-PERP) is resolved to product_id (ETH-PERP-INTX)."""
        mock_pool = _make_mock_pool()
        mock_pool.get_client.return_value.create_order = AsyncMock(
            return_value=_make_order_response()
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ) as mock_get_instrument:
            await _place_order(
                is_paper=False,
                paper_broker=None,
                route=Route.A,
                instrument="ETH-PERP",
                side="BUY",
                size=Decimal("1.0"),
                order_type="LIMIT",
                limit_price=Decimal("2200.00"),
                stop_price=None,
                client_order_id="test-resolve",
                reduce_only=False,
                last_price=Decimal("2205.00"),
                client_pool=mock_pool,
            )

        mock_get_instrument.assert_called_once_with("ETH-PERP")
        call_kwargs = mock_pool.get_client.return_value.create_order.call_args.kwargs
        assert call_kwargs["product_id"] == "ETH-PERP-INTX"


# ---------------------------------------------------------------------------
# TestCancelOrderLive
# ---------------------------------------------------------------------------


class TestCancelOrderLive:
    """_cancel_order() routes to the correct portfolio client in live mode."""

    @pytest.mark.asyncio
    async def test_cancel_routes_to_route_a(self) -> None:
        """cancel_order() calls pool.get_client(A).cancel_order(order_id)."""
        mock_pool = _make_mock_pool(Route.A)

        await _cancel_order(
            order_id="ord-to-cancel",
            route=Route.A,
            is_paper=False,
            client_pool=mock_pool,
        )

        mock_pool.get_client.assert_called_once_with(Route.A)
        mock_pool.get_client.return_value.cancel_order.assert_called_once_with(
            "ord-to-cancel"
        )

    @pytest.mark.asyncio
    async def test_cancel_routes_to_route_b(self) -> None:
        """cancel_order() calls pool.get_client(B).cancel_order(order_id)."""
        mock_pool = _make_mock_pool(Route.B)

        await _cancel_order(
            order_id="ord-to-cancel-b",
            route=Route.B,
            is_paper=False,
            client_pool=mock_pool,
        )

        mock_pool.get_client.assert_called_once_with(Route.B)
        mock_pool.get_client.return_value.cancel_order.assert_called_once_with(
            "ord-to-cancel-b"
        )

    @pytest.mark.asyncio
    async def test_cancel_paper_mode_is_noop(self) -> None:
        """In paper mode, _cancel_order() does nothing (no pool calls)."""
        mock_pool = _make_mock_pool()

        await _cancel_order(
            order_id="paper-ord-1",
            route=Route.A,
            is_paper=True,
            client_pool=mock_pool,
        )

        mock_pool.get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_paper_mode_no_pool_needed(self) -> None:
        """In paper mode, _cancel_order() works with client_pool=None."""
        # Should not raise — paper mode never touches pool
        await _cancel_order(
            order_id="paper-ord-2",
            route=Route.A,
            is_paper=True,
            client_pool=None,
        )


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """_place_order() propagates errors from the REST client."""

    @pytest.mark.asyncio
    async def test_rejected_order_propagates_exception(self) -> None:
        """OrderRejectedError from create_order() bubbles up to the caller."""
        from libs.common.exceptions import OrderRejectedError

        mock_pool = _make_mock_pool()
        mock_pool.get_client.return_value.create_order = AsyncMock(
            side_effect=OrderRejectedError(400, "Insufficient margin", "/orders")
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            with pytest.raises(OrderRejectedError):
                await _place_order(
                    is_paper=False,
                    paper_broker=None,
                    route=Route.A,
                    instrument="ETH-PERP",
                    side="BUY",
                    size=Decimal("1.0"),
                    order_type="LIMIT",
                    limit_price=Decimal("2200.00"),
                    stop_price=None,
                    client_order_id="test-rejected",
                    reduce_only=False,
                    last_price=Decimal("2205.00"),
                    client_pool=mock_pool,
                )

    @pytest.mark.asyncio
    async def test_rate_limit_error_propagates(self) -> None:
        """RateLimitExceededError from create_order() bubbles up."""
        from libs.common.exceptions import RateLimitExceededError

        mock_pool = _make_mock_pool()
        mock_pool.get_client.return_value.create_order = AsyncMock(
            side_effect=RateLimitExceededError(endpoint="/orders", retry_after=5.0)
        )

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            with pytest.raises(RateLimitExceededError):
                await _place_order(
                    is_paper=False,
                    paper_broker=None,
                    route=Route.A,
                    instrument="ETH-PERP",
                    side="BUY",
                    size=Decimal("1.0"),
                    order_type="MARKET",
                    limit_price=None,
                    stop_price=None,
                    client_order_id="test-rate-limited",
                    reduce_only=False,
                    last_price=Decimal("2205.00"),
                    client_pool=mock_pool,
                )

    @pytest.mark.asyncio
    async def test_unknown_instrument_raises_key_error(self) -> None:
        """get_instrument() raising KeyError bubbles up from _place_order()."""
        mock_pool = _make_mock_pool()

        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            with pytest.raises(KeyError, match="UNKNOWN-PERP"):
                await _place_order(
                    is_paper=False,
                    paper_broker=None,
                    route=Route.A,
                    instrument="UNKNOWN-PERP",
                    side="BUY",
                    size=Decimal("1.0"),
                    order_type="LIMIT",
                    limit_price=Decimal("100.00"),
                    stop_price=None,
                    client_order_id="test-unknown",
                    reduce_only=False,
                    last_price=Decimal("100.00"),
                    client_pool=mock_pool,
                )

    @pytest.mark.asyncio
    async def test_live_mode_without_pool_raises(self) -> None:
        """In live mode with client_pool=None, _place_order() raises AssertionError."""
        with patch(
            "agents.execution.main.get_instrument",
            side_effect=_get_instrument_side_effect,
        ):
            with pytest.raises(AssertionError, match="client_pool is required"):
                await _place_order(
                    is_paper=False,
                    paper_broker=None,
                    route=Route.A,
                    instrument="ETH-PERP",
                    side="BUY",
                    size=Decimal("1.0"),
                    order_type="LIMIT",
                    limit_price=Decimal("2200.00"),
                    stop_price=None,
                    client_order_id="test-no-pool",
                    reduce_only=False,
                    last_price=Decimal("2205.00"),
                    client_pool=None,
                )


# ---------------------------------------------------------------------------
# TestPaperModeRegression
# ---------------------------------------------------------------------------


class TestPaperModeRegression:
    """Paper mode must work unchanged after the client_pool param was added."""

    @pytest.mark.asyncio
    async def test_paper_limit_order_fills_immediately(self) -> None:
        """Paper LIMIT order fills at limit_price without touching pool."""
        broker = PaperBroker()
        mock_pool = _make_mock_pool()

        result = await _place_order(
            is_paper=True,
            paper_broker=broker,
            route=Route.A,
            instrument="ETH-PERP",
            side="BUY",
            size=Decimal("1.0"),
            order_type="LIMIT",
            limit_price=Decimal("2200.00"),
            stop_price=None,
            client_order_id="paper-limit",
            reduce_only=False,
            last_price=Decimal("2205.00"),
            client_pool=mock_pool,  # should be ignored
        )

        assert result.status == "FILLED"
        assert result.average_filled_price == "2200.00"
        # Pool must not have been called
        mock_pool.get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_paper_market_order_fills_at_last_price(self) -> None:
        """Paper MARKET order fills at last_price."""
        broker = PaperBroker()

        result = await _place_order(
            is_paper=True,
            paper_broker=broker,
            route=Route.A,
            instrument="ETH-PERP",
            side="BUY",
            size=Decimal("2.0"),
            order_type="MARKET",
            limit_price=None,
            stop_price=None,
            client_order_id="paper-market",
            reduce_only=False,
            last_price=Decimal("2210.00"),
            client_pool=None,
        )

        assert result.status == "FILLED"
        assert result.average_filled_price == "2210.00"

    @pytest.mark.asyncio
    async def test_paper_stop_market_is_open_not_filled(self) -> None:
        """Paper STOP_MARKET order is placed as OPEN (not immediately filled)."""
        broker = PaperBroker()

        result = await _place_order(
            is_paper=True,
            paper_broker=broker,
            route=Route.A,
            instrument="ETH-PERP",
            side="SELL",
            size=Decimal("1.0"),
            order_type="STOP_MARKET",
            limit_price=None,
            stop_price=Decimal("2100.00"),
            client_order_id="paper-stop-market",
            reduce_only=True,
            last_price=Decimal("2150.00"),
            client_pool=None,
        )

        assert result.status == "OPEN"

    @pytest.mark.asyncio
    async def test_paper_mode_without_pool_does_not_raise(self) -> None:
        """Paper mode works with client_pool=None (no credentials needed)."""
        broker = PaperBroker()

        result = await _place_order(
            is_paper=True,
            paper_broker=broker,
            route=Route.B,
            instrument="BTC-PERP",
            side="BUY",
            size=Decimal("0.01"),
            order_type="LIMIT",
            limit_price=Decimal("60000.00"),
            stop_price=None,
            client_order_id="paper-no-pool",
            reduce_only=False,
            last_price=Decimal("60100.00"),
            client_pool=None,
        )

        assert result.status == "FILLED"
