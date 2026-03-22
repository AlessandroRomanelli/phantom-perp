"""Async REST client for Coinbase Advanced API.

Each client instance is bound to a single API key, which is scoped to
one portfolio by Coinbase. Portfolio routing is handled by
CoinbaseClientPool — no portfolio_id is needed in API calls.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.models import (
    CandleResponse,
    FillResponse,
    FundingRateResponse,
    InstrumentResponse,
    OrderBookResponse,
    OrderResponse,
    PortfolioResponse,
    PositionResponse,
)
from libs.coinbase.rate_limiter import RateLimiter
from libs.common.constants import DEFAULT_REST_BASE_URL
from libs.common.exceptions import (
    CoinbaseAPIError,
    InsufficientMarginError,
    OrderRejectedError,
    RateLimitExceededError,
)


class CoinbaseRESTClient:
    """Async HTTP client for Coinbase Advanced REST API.

    Each instance is authenticated with a single API key that is scoped
    to one Coinbase portfolio. Use CoinbaseClientPool to route requests
    to the correct client per portfolio.

    Args:
        auth: CoinbaseAuth instance for request signing.
        base_url: REST API base URL.
        rate_limiter: Shared rate limiter instance.
    """

    def __init__(
        self,
        auth: CoinbaseAuth,
        base_url: str = DEFAULT_REST_BASE_URL,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter or RateLimiter()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> CoinbaseRESTClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an authenticated request against the Coinbase INTX API.

        Args:
            method: HTTP method.
            path: API path (e.g., '/api/v1/orders').
            body: JSON body for POST/PUT requests.
            params: Query parameters for GET requests.

        Returns:
            Parsed JSON response.

        Raises:
            RateLimitExceededError: If rate limited (429).
            InsufficientMarginError: If margin is insufficient for the order.
            CoinbaseAPIError: For other API errors.
        """
        await self._rate_limiter.acquire()

        import orjson

        body_str = orjson.dumps(body).decode() if body else ""
        headers = self._auth.sign(method, path, body_str)
        headers["Content-Type"] = "application/json"

        response = await self._client.request(
            method=method,
            url=path,
            content=body_str.encode() if body_str else None,
            params=params,
            headers=headers,
        )

        # Update rate limiter from response headers
        remaining = response.headers.get("RateLimit-Remaining")
        reset_at = response.headers.get("RateLimit-Reset")
        self._rate_limiter.update_from_headers(
            remaining=int(remaining) if remaining else None,
            reset_at=float(reset_at) if reset_at else None,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitExceededError(
                endpoint=path,
                retry_after=float(retry_after) if retry_after else None,
            )

        if response.status_code >= 400:
            error_body = response.text
            if "insufficient margin" in error_body.lower():
                raise InsufficientMarginError(response.status_code, error_body, path)
            if response.status_code in (400, 422):
                raise OrderRejectedError(response.status_code, error_body, path)
            raise CoinbaseAPIError(response.status_code, error_body, path)

        if response.status_code == 204:
            return None

        return response.json()

    # ── Public (non-portfolio-scoped) endpoints ─────────────────────────

    async def get_instruments(self) -> list[InstrumentResponse]:
        """List all available instruments."""
        data = await self._request("GET", "/api/v1/instruments")
        items = data if isinstance(data, list) else data.get("results", data)
        return [InstrumentResponse.model_validate(item) for item in items]

    async def get_orderbook(
        self,
        instrument_id: str,
        depth: int = 50,
    ) -> OrderBookResponse:
        """Get L2 order book for an instrument.

        Args:
            instrument_id: Instrument identifier.
            depth: Number of levels per side.
        """
        data = await self._request(
            "GET",
            f"/api/v1/instruments/{instrument_id}/book",
            params={"depth": depth},
        )
        return OrderBookResponse.model_validate(data)

    async def get_candles(
        self,
        instrument_id: str,
        granularity: str = "ONE_HOUR",
        start: str | None = None,
        end: str | None = None,
    ) -> list[CandleResponse]:
        """Get OHLCV candles for an instrument.

        Args:
            instrument_id: Instrument identifier.
            granularity: Candle granularity (ONE_MINUTE, FIVE_MINUTE, ONE_HOUR, etc.).
            start: ISO 8601 start time.
            end: ISO 8601 end time.
        """
        params: dict[str, Any] = {"granularity": granularity}
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        data = await self._request(
            "GET",
            f"/api/v1/instruments/{instrument_id}/candles",
            params=params,
        )
        if isinstance(data, dict):
            items = data.get("aggregations", data.get("results", []))
        else:
            items = data
        return [CandleResponse.model_validate(item) for item in items]

    async def get_funding_rate(
        self,
        instrument_id: str,
    ) -> FundingRateResponse:
        """Get current funding rate for an instrument.

        The API returns a paginated response: {"pagination": {...}, "results": [...]}.
        We extract the most recent entry from the results array.

        Args:
            instrument_id: Instrument identifier.
        """
        data = await self._request(
            "GET",
            f"/api/v1/instruments/{instrument_id}/funding",
        )
        results = data.get("results", [data]) if isinstance(data, dict) else [data]
        if not results:
            raise CoinbaseAPIError(200, "Empty funding rate response", f"/api/v1/instruments/{instrument_id}/funding")
        return FundingRateResponse.model_validate(results[0])

    # ── Portfolio-scoped endpoints ──────────────────────────────────────

    async def get_portfolio(self) -> PortfolioResponse:
        """Get portfolio/account summary for this client's portfolio."""
        data = await self._request("GET", "/api/v1/portfolios")
        return PortfolioResponse.model_validate(data)

    async def get_positions(self) -> list[PositionResponse]:
        """Get all positions for this client's portfolio."""
        data = await self._request("GET", "/api/v1/positions")
        return [PositionResponse.model_validate(item) for item in data]

    async def get_open_orders(self) -> list[OrderResponse]:
        """List all open orders for this client's portfolio."""
        data = await self._request(
            "GET",
            "/api/v1/orders",
            params={"status": "OPEN"},
        )
        return [OrderResponse.model_validate(item) for item in data]

    async def create_order(
        self,
        instrument_id: str,
        side: str,
        size: Decimal,
        order_type: str = "LIMIT",
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str = "",
        reduce_only: bool = False,
    ) -> OrderResponse:
        """Place a new order on this client's portfolio.

        Args:
            instrument_id: Instrument to trade.
            side: BUY or SELL.
            size: Order size in base currency (ETH).
            order_type: MARKET, LIMIT, STOP_LIMIT.
            limit_price: Required for LIMIT and STOP_LIMIT orders.
            stop_price: Required for STOP_LIMIT orders.
            client_order_id: Optional client-generated order ID.
            reduce_only: If True, order can only reduce an existing position.
        """
        body: dict[str, Any] = {
            "instrument_id": instrument_id,
            "side": side,
            "type": order_type,
            "size": str(size),
        }
        if limit_price is not None:
            body["price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if client_order_id:
            body["client_order_id"] = client_order_id
        if reduce_only:
            body["reduce_only"] = True

        data = await self._request("POST", "/api/v1/orders", body=body)
        return OrderResponse.model_validate(data)

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order.

        Args:
            order_id: Exchange order ID to cancel.
        """
        await self._request("DELETE", f"/api/v1/orders/{order_id}")

    async def get_fills(
        self,
        instrument_id: str | None = None,
        order_id: str | None = None,
        limit: int = 100,
    ) -> list[FillResponse]:
        """Get fill history for this client's portfolio.

        Args:
            instrument_id: Filter by instrument.
            order_id: Filter by order.
            limit: Maximum number of fills to return.
        """
        params: dict[str, Any] = {"limit": limit}
        if instrument_id:
            params["instrument_id"] = instrument_id
        if order_id:
            params["order_id"] = order_id

        data = await self._request("GET", "/api/v1/fills", params=params)
        return [FillResponse.model_validate(item) for item in data]
