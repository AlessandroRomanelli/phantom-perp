"""Tests for WebSocket market data parsing (Advanced Trade format)."""

from decimal import Decimal

import pytest

from agents.ingestion.sources.ws_market_data import WS_PRODUCT_ID, parse_market_data
from agents.ingestion.state import IngestionState


@pytest.fixture
def state() -> IngestionState:
    return IngestionState()


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
