"""WebSocket client for Coinbase Advanced Trade real-time feeds.

Coinbase Advanced Trade WebSocket protocol:
  - Market data URL: wss://advanced-trade-ws.coinbase.com (no auth needed)
  - User data URL: wss://advanced-trade-ws-user.coinbase.com (JWT auth)
  - Subscribe/unsubscribe type is lowercase ("subscribe", "unsubscribe")
  - One channel per subscribe message (channel is singular, not channels)
  - Product IDs use the -INTX suffix for perps (e.g., "ETH-PERP-INTX")
  - Must send a subscribe within 5 seconds of connecting
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import orjson
import websockets
import websockets.asyncio.client

from libs.common.logging import setup_logging

logger = setup_logging("ws_client", json_output=False)


class CoinbaseWSClient:
    """Async WebSocket client for Coinbase Advanced Trade feeds.

    Supports market data (public) and user data (authenticated) connections.
    Implements auto-reconnect with exponential backoff.

    Args:
        url: WebSocket URL to connect to.
        max_reconnect_delay: Maximum backoff delay in seconds between reconnects.
    """

    def __init__(
        self,
        url: str,
        auth: Any = None,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self._url = url
        self._auth = auth  # Reserved for future JWT auth on user data feed
        self._max_reconnect_delay = max_reconnect_delay
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._subscriptions: list[dict[str, Any]] = []
        self._running = False
        self._reconnect_delay = 1.0
        self._on_message: Callable[[dict[str, Any]], Any] | None = None

    async def connect(self) -> None:
        """Establish the WebSocket connection."""
        self._ws = await websockets.asyncio.client.connect(
            self._url,
            ping_interval=20,
            ping_timeout=10,
        )
        self._reconnect_delay = 1.0
        logger.info("ws_connected", url=self._url)

        # Re-subscribe to any channels on reconnect
        for sub in self._subscriptions:
            await self._send(sub)

    async def _send(self, message: dict[str, Any]) -> None:
        """Send a JSON message over the WebSocket."""
        if self._ws is None:
            raise ConnectionError("WebSocket is not connected")
        await self._ws.send(orjson.dumps(message).decode())

    async def subscribe(
        self,
        channels: list[str],
        product_ids: list[str] | None = None,
    ) -> None:
        """Subscribe to one or more channels.

        Sends one subscribe message per channel (Advanced Trade format).

        Args:
            channels: Channel names (e.g., ['ticker', 'level2', 'market_trades']).
            product_ids: Product IDs to filter (e.g., ['ETH-PERP-INTX']).
        """
        for channel in channels:
            msg: dict[str, Any] = {
                "type": "subscribe",
                "channel": channel,
            }
            if product_ids:
                msg["product_ids"] = product_ids

            self._subscriptions.append(msg)

            if self._ws is not None:
                await self._send(msg)
                logger.info("ws_subscribed", channel=channel, products=product_ids)

    async def unsubscribe(self, channels: list[str]) -> None:
        """Unsubscribe from channels.

        Args:
            channels: Channel names to unsubscribe from.
        """
        for channel in channels:
            msg: dict[str, Any] = {
                "type": "unsubscribe",
                "channel": channel,
            }
            if self._ws is not None:
                await self._send(msg)

        self._subscriptions = [
            s for s in self._subscriptions if s.get("channel") not in channels
        ]

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """Yield messages from the WebSocket with auto-reconnect.

        Yields parsed JSON messages. On disconnect, automatically reconnects
        with exponential backoff up to max_reconnect_delay.
        """
        self._running = True
        while self._running:
            try:
                if self._ws is None:
                    await self.connect()

                assert self._ws is not None
                async for raw_msg in self._ws:
                    if isinstance(raw_msg, bytes):
                        data = orjson.loads(raw_msg)
                    else:
                        data = orjson.loads(raw_msg.encode())

                    yield data

            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.ConnectionClosedError,
                ConnectionError,
                OSError,
            ) as e:
                logger.warning(
                    "ws_disconnected",
                    url=self._url,
                    error=str(e),
                    reconnect_delay=self._reconnect_delay,
                )
                self._ws = None
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self._max_reconnect_delay,
                    )

    async def close(self) -> None:
        """Close the WebSocket connection and stop listening."""
        self._running = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            logger.info("ws_closed", url=self._url)

    @staticmethod
    def extract_portfolio_id(message: dict[str, Any]) -> str | None:
        """Extract portfolio_id from a user-data WebSocket event.

        Args:
            message: Parsed WebSocket message.

        Returns:
            Portfolio UUID string if present, None for market data events.
        """
        return message.get("portfolio_id") or message.get("portfolioId")

    @staticmethod
    def tag_with_portfolio(
        message: dict[str, Any],
        portfolio_id: str,
    ) -> dict[str, Any]:
        """Tag a message with a portfolio_id for downstream routing.

        Args:
            message: Original message dict.
            portfolio_id: Portfolio UUID to tag.

        Returns:
            New dict with portfolio_id added.
        """
        return {**message, "portfolio_id": portfolio_id}
