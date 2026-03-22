"""Tests for WebSocket market data parsing (Advanced Trade format)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.ingestion.sources.ws_market_data import (
    _dispatch_message,
    _extract_product_ids,
    _mark_stale_instruments,
    parse_market_data,
    run_ws_market_data,
)
from agents.ingestion.state import IngestionState
from libs.common.constants import STALE_DATA_HALT_SECONDS

WS_PRODUCT_ID = "ETH-PERP-INTX"


@pytest.fixture
def state() -> IngestionState:
    return IngestionState(instrument_id="ETH-PERP")


class TestParseMarketData:
    def test_ticker_updates_prices(self, state: IngestionState) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": WS_PRODUCT_ID,
                            "best_bid": "2230.50",
                            "best_ask": "2231.00",
                            "price": "2230.75",
                            "volume_24_h": "15000.5",
                        }
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is True
        assert state.best_bid == Decimal("2230.50")
        assert state.best_ask == Decimal("2231.00")
        assert state.last_price == Decimal("2230.75")
        assert state.volume_24h == Decimal("15000.5")
        assert state.last_ws_update is not None

    def test_ignores_wrong_instrument(self, state: IngestionState) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "BTC-PERP-INTX",
                            "best_bid": "65000.00",
                            "best_ask": "65001.00",
                            "price": "65000.50",
                        }
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is False
        assert state.best_bid is None

    def test_l2_snapshot_updates_depth(self, state: IngestionState) -> None:
        msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": WS_PRODUCT_ID,
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "10.5"},
                        {"side": "bid", "price_level": "2230.00", "new_quantity": "25.0"},
                        {"side": "bid", "price_level": "2229.50", "new_quantity": "8.2"},
                        {"side": "offer", "price_level": "2231.00", "new_quantity": "12.0"},
                        {"side": "offer", "price_level": "2231.50", "new_quantity": "20.0"},
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is True
        assert len(state.bid_depth) == 3
        assert len(state.ask_depth) == 2
        assert state.bid_depth[0].price == Decimal("2230.50")
        assert state.bid_depth[0].size == Decimal("10.5")
        assert state.ask_depth[0].price == Decimal("2231.00")
        assert state.best_bid == Decimal("2230.50")
        assert state.best_ask == Decimal("2231.00")

    def test_l2_incremental_update(self, state: IngestionState) -> None:
        # Start with a snapshot
        snapshot_msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": WS_PRODUCT_ID,
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "10.0"},
                        {"side": "offer", "price_level": "2231.00", "new_quantity": "12.0"},
                    ],
                }
            ],
        }
        parse_market_data(snapshot_msg, state)

        # Apply an incremental update (modify bid quantity)
        update_msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "update",
                    "product_id": WS_PRODUCT_ID,
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "15.0"},
                    ],
                }
            ],
        }
        updated = parse_market_data(update_msg, state)

        assert updated is True
        assert state.bid_depth[0].size == Decimal("15.0")

    def test_l2_remove_level(self, state: IngestionState) -> None:
        # Snapshot with 2 bid levels
        snapshot_msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": WS_PRODUCT_ID,
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "10.0"},
                        {"side": "bid", "price_level": "2230.00", "new_quantity": "20.0"},
                    ],
                }
            ],
        }
        parse_market_data(snapshot_msg, state)
        assert len(state.bid_depth) == 2

        # Remove top level (quantity = 0)
        update_msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "update",
                    "product_id": WS_PRODUCT_ID,
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "0"},
                    ],
                }
            ],
        }
        parse_market_data(update_msg, state)

        assert len(state.bid_depth) == 1
        assert state.best_bid == Decimal("2230.00")

    def test_market_trades_updates_last_price(self, state: IngestionState) -> None:
        msg = {
            "channel": "market_trades",
            "events": [
                {
                    "type": "update",
                    "trades": [
                        {
                            "product_id": WS_PRODUCT_ID,
                            "price": "2230.75",
                            "size": "0.5",
                            "side": "BUY",
                        }
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is True
        assert state.last_price == Decimal("2230.75")

    def test_partial_ticker_update(self, state: IngestionState) -> None:
        """Only some fields present — should still update what's available."""
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": WS_PRODUCT_ID,
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is True
        assert state.last_price == Decimal("2230.75")
        assert state.best_bid is None  # Not provided

    def test_invalid_decimal_skipped(self, state: IngestionState) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": WS_PRODUCT_ID,
                            "best_bid": "not_a_number",
                            "best_ask": "2231.00",
                        }
                    ],
                }
            ],
        }

        updated = parse_market_data(msg, state)

        assert updated is True
        assert state.best_bid is None  # Invalid, skipped
        assert state.best_ask == Decimal("2231.00")

    def test_subscriptions_no_update(self, state: IngestionState) -> None:
        msg = {"channel": "subscriptions"}
        updated = parse_market_data(msg, state)
        assert updated is False

    def test_heartbeats_no_update(self, state: IngestionState) -> None:
        msg = {"channel": "heartbeats"}
        updated = parse_market_data(msg, state)
        assert updated is False

    def test_empty_events_no_update(self, state: IngestionState) -> None:
        msg = {"channel": "ticker", "events": []}
        updated = parse_market_data(msg, state)
        assert updated is False


# ── Readiness flag tests ─────────────────────────────────────────────


class TestReadinessFlags:
    def test_is_ready_all_false_by_default(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        assert state.has_ws_tick is False
        assert state.has_candles is False
        assert state.has_funding is False
        assert state.is_ready() is False

    def test_is_ready_true_when_all_set(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = True
        state.has_candles = True
        state.has_funding = True
        assert state.is_ready() is True

    def test_is_ready_false_when_ws_missing(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_candles = True
        state.has_funding = True
        assert state.is_ready() is False

    def test_is_ready_false_when_candles_missing(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = True
        state.has_funding = True
        assert state.is_ready() is False

    def test_is_ready_false_when_funding_missing(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = True
        state.has_candles = True
        assert state.is_ready() is False


# ── Multi-instrument dispatch tests ──────────────────────────────────


class TestMultiInstrumentDispatch:
    @pytest.fixture()
    def states(self) -> dict[str, IngestionState]:
        return {
            "ETH-PERP": IngestionState(instrument_id="ETH-PERP"),
            "BTC-PERP": IngestionState(instrument_id="BTC-PERP"),
        }

    @pytest.fixture()
    def product_to_instrument(self) -> dict[str, str]:
        return {"ETH-PERP-INTX": "ETH-PERP", "BTC-PERP-INTX": "BTC-PERP"}

    def test_dispatch_ticker_to_correct_instrument(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "best_bid": "2230.50",
                            "best_ask": "2231.00",
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert "ETH-PERP" in updated
        assert states["ETH-PERP"].best_bid == Decimal("2230.50")
        assert states["BTC-PERP"].best_bid is None

    def test_dispatch_l2_to_correct_instrument(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "ETH-PERP-INTX",
                    "updates": [
                        {"side": "bid", "price_level": "2230.50", "new_quantity": "10.0"},
                    ],
                }
            ],
        }
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert "ETH-PERP" in updated
        assert len(states["ETH-PERP"].bid_depth) == 1
        assert len(states["BTC-PERP"].bid_depth) == 0

    def test_dispatch_trades_to_correct_instrument(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {
            "channel": "market_trades",
            "events": [
                {
                    "type": "update",
                    "trades": [
                        {
                            "product_id": "BTC-PERP-INTX",
                            "price": "65000.50",
                            "size": "0.1",
                            "side": "BUY",
                        }
                    ],
                }
            ],
        }
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert "BTC-PERP" in updated
        assert states["BTC-PERP"].last_price == Decimal("65000.50")
        assert states["ETH-PERP"].last_price is None

    def test_dispatch_multi_product_ticker(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "price": "2230.75",
                        },
                        {
                            "product_id": "BTC-PERP-INTX",
                            "price": "65000.50",
                        },
                    ],
                }
            ],
        }
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert "ETH-PERP" in updated
        assert "BTC-PERP" in updated
        assert states["ETH-PERP"].last_price == Decimal("2230.75")
        assert states["BTC-PERP"].last_price == Decimal("65000.50")

    def test_dispatch_unrecognized_product_id(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "SOL-PERP-INTX",
                            "price": "150.00",
                        }
                    ],
                }
            ],
        }
        with caplog.at_level(logging.WARNING):
            updated = _dispatch_message(msg, states, product_to_instrument)
        assert updated == []
        assert "unrecognized_product_id" in caplog.text

    def test_dispatch_sets_has_ws_tick_on_first_update(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        assert states["ETH-PERP"].has_ws_tick is False
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }
        _dispatch_message(msg, states, product_to_instrument)
        assert states["ETH-PERP"].has_ws_tick is True

    def test_dispatch_logs_instrument_ws_ready(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }
        with caplog.at_level(logging.INFO):
            _dispatch_message(msg, states, product_to_instrument)
        assert "instrument_ws_ready" in caplog.text

    def test_dispatch_skips_heartbeats(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {"channel": "heartbeats", "events": []}
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert updated == []

    def test_dispatch_skips_subscriptions(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {"channel": "subscriptions", "events": []}
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert updated == []

    def test_dispatch_returns_updated_instrument_ids(
        self,
        states: dict[str, IngestionState],
        product_to_instrument: dict[str, str],
    ) -> None:
        msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }
        updated = _dispatch_message(msg, states, product_to_instrument)
        assert isinstance(updated, list)
        assert updated == ["ETH-PERP"]


# ── Multi-instrument subscribe tests ─────────────────────────────────


class TestMultiInstrumentSubscribe:
    @pytest.mark.asyncio()
    async def test_subscribe_all_products(self) -> None:
        ws_client = MagicMock()
        ws_client.subscribe = AsyncMock()
        # Make listen() return an empty async iterator
        ws_client.listen = MagicMock(return_value=_async_iter([]))

        states = {
            "ETH-PERP": IngestionState(instrument_id="ETH-PERP"),
            "BTC-PERP": IngestionState(instrument_id="BTC-PERP"),
        }
        product_to_instrument = {
            "ETH-PERP-INTX": "ETH-PERP",
            "BTC-PERP-INTX": "BTC-PERP",
        }

        await run_ws_market_data(ws_client, states, product_to_instrument)

        ws_client.subscribe.assert_awaited_once()
        call_kwargs = ws_client.subscribe.call_args
        product_ids = call_kwargs.kwargs.get("product_ids") or call_kwargs[1].get("product_ids")
        if product_ids is None:
            # positional args
            product_ids = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs.kwargs["product_ids"]
        assert set(product_ids) == {"ETH-PERP-INTX", "BTC-PERP-INTX"}

    @pytest.mark.asyncio()
    async def test_on_update_called_with_instrument_id(self) -> None:
        ws_client = MagicMock()
        ws_client.subscribe = AsyncMock()

        eth_ticker_msg = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "product_id": "ETH-PERP-INTX",
                            "price": "2230.75",
                        }
                    ],
                }
            ],
        }
        ws_client.listen = MagicMock(return_value=_async_iter([eth_ticker_msg]))

        states = {
            "ETH-PERP": IngestionState(instrument_id="ETH-PERP"),
        }
        product_to_instrument = {"ETH-PERP-INTX": "ETH-PERP"}

        on_update = AsyncMock()

        await run_ws_market_data(
            ws_client, states, product_to_instrument, on_update=on_update,
        )

        on_update.assert_awaited_with("ETH-PERP")


# ── Reconnect staleness tests ────────────────────────────────────────


class TestReconnectStaleness:
    def test_mark_stale_instruments_resets_has_ws_tick(self) -> None:
        now = datetime.now(UTC)
        stale_state = IngestionState(instrument_id="ETH-PERP")
        stale_state.has_ws_tick = True
        stale_state.last_ws_update = now - timedelta(seconds=STALE_DATA_HALT_SECONDS + 30)

        fresh_state = IngestionState(instrument_id="BTC-PERP")
        fresh_state.has_ws_tick = True
        fresh_state.last_ws_update = now - timedelta(seconds=5)

        states = {"ETH-PERP": stale_state, "BTC-PERP": fresh_state}
        _mark_stale_instruments(states)

        assert stale_state.has_ws_tick is False
        assert fresh_state.has_ws_tick is True

    def test_mark_stale_instruments_skips_already_not_ready(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = False
        state.last_ws_update = None

        states = {"ETH-PERP": state}
        _mark_stale_instruments(states)

        # Should remain unchanged
        assert state.has_ws_tick is False

    def test_mark_stale_instruments_logs_instrument_ws_stale(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = datetime.now(UTC)
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = True
        state.last_ws_update = now - timedelta(seconds=STALE_DATA_HALT_SECONDS + 30)

        states = {"ETH-PERP": state}
        with caplog.at_level(logging.WARNING):
            _mark_stale_instruments(states)

        assert "instrument_ws_stale" in caplog.text

    def test_mark_stale_instruments_handles_no_timestamp(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        state.has_ws_tick = True
        state.last_ws_update = None  # No timestamp but has_ws_tick is True

        states = {"ETH-PERP": state}
        _mark_stale_instruments(states)

        assert state.has_ws_tick is False


# ── Helpers ──────────────────────────────────────────────────────────


async def _async_iter(items: list[dict[str, object]]) -> None:
    """Create an async iterator from a list (used as mock ws_client.listen())."""
    for item in items:
        yield item
