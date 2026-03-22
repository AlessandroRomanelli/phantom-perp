"""WebSocket market data handler for multi-instrument ingestion.

Connects to the Coinbase Advanced Trade WebSocket feed, subscribes to
ticker (top-of-book), level2 (orderbook), and market_trades channels
for all configured instruments, dispatches messages to per-instrument
IngestionState objects, and invokes on_update callbacks.

Coinbase Advanced Trade WebSocket channels:
  ticker        -- best bid/ask, last price, volume, 24h stats
  level2        -- L2 orderbook snapshots and incremental updates
  market_trades -- individual trade executions (price, size, side)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from libs.coinbase.ws_client import CoinbaseWSClient
from libs.common.constants import STALE_DATA_HALT_SECONDS
from libs.common.logging import setup_logging
from libs.common.utils import utc_now

from agents.ingestion.state import BookLevel, IngestionState

logger = setup_logging("ws_market_data", json_output=False)


def _to_decimal(value: Any) -> Decimal | None:
    """Safely convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _extract_product_ids(message: dict[str, Any], channel: str) -> set[str]:
    """Extract all unique product IDs from a WebSocket message.

    Different channels embed product_id at different levels:
      - ticker: events[].tickers[].product_id
      - market_trades: events[].trades[].product_id
      - l2_data: events[].product_id

    Args:
        message: Parsed JSON message from the WebSocket.
        channel: Channel name (ticker, l2_data, market_trades).

    Returns:
        Set of unique product ID strings found in the message.
    """
    product_ids: set[str] = set()
    events = message.get("events", [])

    for event in events:
        if channel == "ticker":
            for ticker in event.get("tickers", []):
                pid = ticker.get("product_id")
                if pid:
                    product_ids.add(pid)
        elif channel == "market_trades":
            for trade in event.get("trades", []):
                pid = trade.get("product_id")
                if pid:
                    product_ids.add(pid)
        elif channel == "l2_data":
            pid = event.get("product_id")
            if pid:
                product_ids.add(pid)

    return product_ids


def _dispatch_message(
    message: dict[str, Any],
    states: dict[str, IngestionState],
    product_to_instrument: dict[str, str],
) -> list[str]:
    """Route a WebSocket message to the correct per-instrument state(s).

    Extracts product IDs from the message, maps each to an instrument ID,
    and calls parse_market_data to update the corresponding state.

    Args:
        message: Parsed JSON message from the WebSocket.
        states: Per-instrument IngestionState objects keyed by instrument ID.
        product_to_instrument: Mapping from WS product ID to instrument ID.

    Returns:
        List of instrument IDs that were updated.
    """
    channel = message.get("channel", "")

    # Skip non-data channels
    if channel in ("", "subscriptions", "heartbeats"):
        return []

    product_ids = _extract_product_ids(message, channel)
    if not product_ids:
        return []

    updated_instruments: list[str] = []

    for product_id in product_ids:
        instrument_id = product_to_instrument.get(product_id)
        if instrument_id is None:
            logger.warning("unrecognized_product_id", product_id=product_id)
            continue

        state = states.get(instrument_id)
        if state is None:
            continue

        changed = parse_market_data(message, state, product_id)
        if changed:
            if not state.has_ws_tick:
                state.has_ws_tick = True
                logger.info("instrument_ws_ready", instrument=instrument_id)
            if instrument_id not in updated_instruments:
                updated_instruments.append(instrument_id)

    return updated_instruments


def _mark_stale_instruments(
    states: dict[str, IngestionState],
) -> None:
    """After WS reconnect, mark instruments stale if no data arrived within threshold.

    Checks each instrument's last_ws_update against STALE_DATA_HALT_SECONDS.
    Instruments that haven't received WS data within the threshold get
    has_ws_tick reset to False, which prevents snapshot publishing via
    the is_ready() gate until fresh data arrives (D-10).
    """
    now = datetime.now(UTC)
    for instrument_id, state in states.items():
        if not state.has_ws_tick:
            continue  # Already not ready
        if state.last_ws_update is None:
            # has_ws_tick is True but no timestamp -- shouldn't happen, mark stale
            state.has_ws_tick = False
            logger.warning(
                "instrument_ws_stale", instrument=instrument_id, reason="no_timestamp",
            )
            continue
        elapsed = (now - state.last_ws_update).total_seconds()
        if elapsed > STALE_DATA_HALT_SECONDS:
            state.has_ws_tick = False
            logger.warning(
                "instrument_ws_stale",
                instrument=instrument_id,
                elapsed_seconds=round(elapsed, 1),
                threshold=STALE_DATA_HALT_SECONDS,
            )


def parse_market_data(
    message: dict[str, Any],
    state: IngestionState,
    ws_product_id: str = "ETH-PERP-INTX",
) -> bool:
    """Parse an Advanced Trade WebSocket message and update shared state.

    Advanced Trade messages wrap data in an events array:
      {"channel": "ticker", "events": [{"type": "snapshot", "tickers": [...]}]}

    Args:
        message: Parsed JSON message from the WebSocket.
        state: Shared ingestion state to update.
        ws_product_id: Product ID to filter for in this instrument's data.

    Returns:
        True if state was updated (i.e., a snapshot should be published).
    """
    channel = message.get("channel", "")

    # Skip non-data messages
    if channel in ("", "subscriptions", "heartbeats"):
        return False

    events = message.get("events", [])
    if not events:
        return False

    updated = False

    for event in events:
        if channel == "ticker":
            updated |= _update_ticker(event, state, ws_product_id)
        elif channel == "l2_data":
            updated |= _update_orderbook(event, state, ws_product_id)
        elif channel == "market_trades":
            updated |= _update_trades(event, state, ws_product_id)

    if updated:
        state.last_ws_update = utc_now()

    return updated


def _update_ticker(event: dict[str, Any], state: IngestionState, ws_product_id: str) -> bool:
    """Extract top-of-book and ticker fields from a ticker event.

    Ticker event format:
      {"type": "snapshot", "tickers": [{"product_id": "...", "price": "...", ...}]}
    """
    tickers = event.get("tickers", [])
    changed = False

    for ticker in tickers:
        product_id = ticker.get("product_id", "")
        if product_id != ws_product_id:
            continue

        for ws_key, attr in [
            ("best_bid", "best_bid"),
            ("best_ask", "best_ask"),
            ("price", "last_price"),
            ("volume_24_h", "volume_24h"),
        ]:
            raw = ticker.get(ws_key)
            if raw is not None:
                val = _to_decimal(raw)
                if val is not None:
                    setattr(state, attr, val)
                    changed = True

    return changed


def _update_trades(event: dict[str, Any], state: IngestionState, ws_product_id: str) -> bool:
    """Extract last trade price from a market_trades event.

    Trade event format:
      {"type": "snapshot", "trades": [{"product_id": "...", "price": "...", ...}]}
    """
    trades = event.get("trades", [])
    changed = False

    for trade in trades:
        product_id = trade.get("product_id", "")
        if product_id != ws_product_id:
            continue

        price = _to_decimal(trade.get("price"))
        if price is not None:
            state.last_price = price
            changed = True

    return changed


def _update_orderbook(event: dict[str, Any], state: IngestionState, ws_product_id: str) -> bool:
    """Extract L2 book data from a level2 event.

    Level2 event format:
      {"type": "snapshot|update", "product_id": "...",
       "updates": [{"side": "bid", "price_level": "...", "new_quantity": "..."}]}
    """
    product_id = event.get("product_id", "")
    if product_id != ws_product_id:
        return False

    updates = event.get("updates", [])
    if not updates:
        return False

    event_type = event.get("type", "")

    if event_type == "snapshot":
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []

        for update in updates:
            price = _to_decimal(update.get("price_level"))
            size = _to_decimal(update.get("new_quantity"))
            if price is None or size is None:
                continue
            side = update.get("side", "")
            if side == "bid":
                bids.append(BookLevel(price=price, size=size))
            elif side in ("ask", "offer"):
                asks.append(BookLevel(price=price, size=size))

        if bids:
            bids.sort(key=lambda lvl: lvl.price, reverse=True)
            state.bid_depth = bids
            state.best_bid = bids[0].price
        if asks:
            asks.sort(key=lambda lvl: lvl.price)
            state.ask_depth = asks
            state.best_ask = asks[0].price
    else:
        # Incremental update
        for update in updates:
            price = _to_decimal(update.get("price_level"))
            size = _to_decimal(update.get("new_quantity"))
            if price is None or size is None:
                continue
            side = update.get("side", "")
            if side == "bid":
                _apply_level_update(state.bid_depth, price, size, reverse=True)
                if state.bid_depth:
                    state.best_bid = state.bid_depth[0].price
            elif side in ("ask", "offer"):
                _apply_level_update(state.ask_depth, price, size, reverse=False)
                if state.ask_depth:
                    state.best_ask = state.ask_depth[0].price

    return True


def _apply_level_update(
    levels: list[BookLevel], price: Decimal, size: Decimal, reverse: bool,
) -> None:
    """Apply an incremental level update to the orderbook."""
    for i, level in enumerate(levels):
        if level.price == price:
            if size == 0:
                levels.pop(i)
            else:
                levels[i] = BookLevel(price=price, size=size)
            return

    # New level -- insert in sorted order
    if size > 0:
        levels.append(BookLevel(price=price, size=size))
        levels.sort(key=lambda lvl: lvl.price, reverse=reverse)


async def run_ws_market_data(
    ws_client: CoinbaseWSClient,
    states: dict[str, IngestionState],
    product_to_instrument: dict[str, str],
    on_update: Callable[[str], Any] | None = None,
) -> None:
    """Run the WebSocket market data listener for all instruments.

    Connects to the Coinbase Advanced Trade market data feed, subscribes to
    ticker, level2, and market_trades channels for all configured product IDs,
    dispatches messages to per-instrument states, and invokes on_update
    callbacks with the instrument ID.

    Args:
        ws_client: Configured CoinbaseWSClient for market data URL.
        states: Per-instrument IngestionState objects keyed by instrument ID.
        product_to_instrument: Mapping from WS product ID to instrument ID.
        on_update: Optional async callback invoked with instrument_id after each
            state update.
    """
    await ws_client.subscribe(
        channels=["ticker", "level2", "market_trades"],
        product_ids=list(product_to_instrument.keys()),
    )

    _last_stale_check = time.monotonic()

    async for message in ws_client.listen():
        updated_instruments = _dispatch_message(message, states, product_to_instrument)

        for instrument_id in updated_instruments:
            if on_update is not None:
                await on_update(instrument_id)

        # Periodic staleness check (D-10): every STALE_DATA_HALT_SECONDS
        now = time.monotonic()
        if now - _last_stale_check > STALE_DATA_HALT_SECONDS:
            _mark_stale_instruments(states)
            _last_stale_check = now
