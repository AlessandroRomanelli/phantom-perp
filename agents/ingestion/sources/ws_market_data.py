"""WebSocket market data handler for ETH-PERP.

Connects to the Coinbase Advanced Trade WebSocket feed, subscribes to
ticker (top-of-book), level2 (orderbook), and market_trades channels
for ETH-PERP-INTX, and updates the shared IngestionState on every tick.

Coinbase Advanced Trade WebSocket channels:
  ticker        — best bid/ask, last price, volume, 24h stats
  level2        — L2 orderbook snapshots and incremental updates
  market_trades — individual trade executions (price, size, side)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from libs.coinbase.ws_client import CoinbaseWSClient
from libs.common.utils import utc_now

from agents.ingestion.state import BookLevel, IngestionState

def _to_decimal(value: Any) -> Decimal | None:
    """Safely convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


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

    # New level — insert in sorted order
    if size > 0:
        levels.append(BookLevel(price=price, size=size))
        levels.sort(key=lambda lvl: lvl.price, reverse=reverse)


async def run_ws_market_data(
    ws_client: CoinbaseWSClient,
    state: IngestionState,
    on_update: Any = None,
    ws_product_id: str = "ETH-PERP-INTX",
) -> None:
    """Run the WebSocket market data listener.

    Connects to the Coinbase Advanced Trade market data feed, subscribes to
    ticker, level2, and market_trades channels for the given product, and
    continuously updates shared state.

    Args:
        ws_client: Configured CoinbaseWSClient for market data URL.
        state: Shared ingestion state to update.
        on_update: Optional async callback invoked after each state update.
        ws_product_id: WebSocket product ID (e.g., "ETH-PERP-INTX").
    """
    await ws_client.subscribe(
        channels=["ticker", "level2", "market_trades"],
        product_ids=[ws_product_id],
    )

    async for message in ws_client.listen():
        if parse_market_data(message, state, ws_product_id):
            if on_update is not None:
                await on_update()
