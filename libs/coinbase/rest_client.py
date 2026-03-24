"""Async REST client for Coinbase Advanced Trade API.

Each client instance is bound to a single API key, which is scoped to
one portfolio by Coinbase. Portfolio routing is handled by
CoinbaseClientPool -- no portfolio_id is needed in API calls.

Portfolio-scoped endpoints (positions, portfolio summary) use
portfolio_uuid injected at construction time.
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
    OrderBookResponse,
    OrderResponse,
    PortfolioResponse,
    PositionResponse,
    ProductResponse,
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
    """Async HTTP client for Coinbase Advanced Trade REST API.

    Each instance is authenticated with a single API key that is scoped
    to one Coinbase portfolio. Use CoinbaseClientPool to route requests
    to the correct client per portfolio.

    Args:
        auth: CoinbaseAuth instance for request signing.
        base_url: REST API base URL.
        rate_limiter: Shared rate limiter instance.
        portfolio_uuid: Portfolio UUID for portfolio-scoped endpoints.
    """

    def __init__(
        self,
        auth: CoinbaseAuth,
        base_url: str = DEFAULT_REST_BASE_URL,
        rate_limiter: RateLimiter | None = None,
        portfolio_uuid: str = "",
    ) -> None:
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter or RateLimiter()
        self._portfolio_uuid = portfolio_uuid
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
        """Execute an authenticated request against the Coinbase Advanced Trade API.

        Args:
            method: HTTP method.
            path: API path (e.g., '/api/v3/brokerage/orders').
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

    # -- Public (non-portfolio-scoped) endpoints ----------------------------

    async def get_products(
        self,
        product_type: str | None = None,
        contract_expiry_type: str | None = None,
    ) -> list[ProductResponse]:
        """List available products, optionally filtered.

        Args:
            product_type: Filter by product type (e.g., 'FUTURE').
            contract_expiry_type: Filter by expiry type (e.g., 'PERPETUAL').
        """
        params: dict[str, Any] = {}
        if product_type:
            params["product_type"] = product_type
        if contract_expiry_type:
            params["contract_expiry_type"] = contract_expiry_type
        data = await self._request("GET", "/api/v3/brokerage/products", params=params)
        items = data.get("products", []) if isinstance(data, dict) else data
        return [ProductResponse.model_validate(item) for item in items]

    async def get_instruments(self) -> list[ProductResponse]:
        """Alias for get_products() for backward compatibility."""
        return await self.get_products()

    async def get_orderbook(
        self,
        product_id: str,
        limit: int = 50,
    ) -> OrderBookResponse:
        """Get L2 order book for a product.

        Args:
            product_id: Product identifier (e.g., 'ETH-PERP-INTX').
            limit: Number of levels per side.
        """
        data = await self._request(
            "GET",
            "/api/v3/brokerage/product_book",
            params={"product_id": product_id, "limit": limit},
        )
        pricebook = data.get("pricebook", data) if isinstance(data, dict) else data
        return OrderBookResponse.model_validate(pricebook)

    async def get_candles(
        self,
        product_id: str,
        granularity: str = "ONE_HOUR",
        start: str | None = None,
        end: str | None = None,
    ) -> list[CandleResponse]:
        """Get OHLCV candles for a product.

        Args:
            product_id: Product identifier (e.g., 'ETH-PERP-INTX').
            granularity: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, ONE_HOUR, etc.
            start: UNIX timestamp string for start time.
            end: UNIX timestamp string for end time.
        """
        params: dict[str, Any] = {"granularity": granularity}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = await self._request(
            "GET",
            f"/api/v3/brokerage/products/{product_id}/candles",
            params=params,
        )
        items = data.get("candles", []) if isinstance(data, dict) else data
        return [CandleResponse.model_validate(item) for item in items]

    async def get_funding_rate(
        self,
        product_id: str,
    ) -> FundingRateResponse:
        """Get current funding rate for a perpetual contract.

        Advanced Trade has no dedicated funding endpoint. Funding rate
        is embedded in the product details response under
        future_product_details.perpetual_details.

        Args:
            product_id: Product identifier (e.g., 'ETH-PERP-INTX').
        """
        data = await self._request(
            "GET",
            f"/api/v3/brokerage/products/{product_id}",
        )
        future_details = (data.get("future_product_details") or {})
        perp_details = future_details.get("perpetual_details") or {}
        funding_rate = perp_details.get("funding_rate", "0")
        mark_price_str = data.get("price", "0")
        return FundingRateResponse(
            product_id=product_id,
            funding_rate=Decimal(str(funding_rate)),
            mark_price=Decimal(str(mark_price_str)),
        )

    # -- Portfolio-scoped endpoints -----------------------------------------

    async def get_portfolio(self) -> PortfolioResponse:
        """Get portfolio summary for this client's portfolio."""
        data = await self._request(
            "GET",
            f"/api/v3/brokerage/intx/portfolio/{self._portfolio_uuid}",
        )
        # The response has "portfolios" (array) and "summary" (object).
        # Portfolio-level fields (collateral, margin, uuid) are in portfolios[0].
        if isinstance(data, dict):
            portfolios = data.get("portfolios", [])
            portfolio_data = portfolios[0] if portfolios else data
        else:
            portfolio_data = data
        return PortfolioResponse.model_validate(portfolio_data)

    async def get_positions(self) -> list[PositionResponse]:
        """Get all positions for this client's portfolio."""
        data = await self._request(
            "GET",
            f"/api/v3/brokerage/intx/positions/{self._portfolio_uuid}",
        )
        items = data.get("positions", []) if isinstance(data, dict) else data
        return [PositionResponse.model_validate(item) for item in items]

    async def get_open_orders(self) -> list[OrderResponse]:
        """List all open orders."""
        data = await self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/batch",
            params={"order_status": "OPEN"},
        )
        items = data.get("orders", []) if isinstance(data, dict) else data
        return [OrderResponse.model_validate(item) for item in items]

    async def create_order(
        self,
        product_id: str,
        side: str,
        size: Decimal,
        order_type: str = "LIMIT",
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str = "",
        reduce_only: bool = False,
    ) -> OrderResponse:
        """Place a new order.

        Args:
            product_id: Product to trade (e.g., 'ETH-PERP-INTX').
            side: BUY or SELL.
            size: Order size in base currency.
            order_type: MARKET, LIMIT, STOP_LIMIT.
            limit_price: Required for LIMIT and STOP_LIMIT orders.
            stop_price: Required for STOP_LIMIT orders.
            client_order_id: Client-generated order ID (required by Advanced Trade).
            reduce_only: If True, order can only reduce an existing position.
        """
        import uuid as uuid_mod

        order_config: dict[str, Any] = {}
        if order_type == "MARKET":
            order_config["market_market_ioc"] = {"base_size": str(size)}
        elif order_type == "LIMIT":
            order_config["limit_limit_gtc"] = {
                "base_size": str(size),
                "limit_price": str(limit_price) if limit_price else "0",
            }
        elif order_type == "STOP_LIMIT":
            order_config["stop_limit_stop_limit_gtc"] = {
                "base_size": str(size),
                "limit_price": str(limit_price) if limit_price else "0",
                "stop_price": str(stop_price) if stop_price else "0",
            }

        body: dict[str, Any] = {
            "product_id": product_id,
            "side": side,
            "client_order_id": client_order_id or str(uuid_mod.uuid4()),
            "order_configuration": order_config,
        }
        if reduce_only:
            body["leverage"] = "1"  # Advanced Trade reduce-only via leverage

        data = await self._request("POST", "/api/v3/brokerage/orders", body=body)

        # Advanced Trade wraps success in {"success": true, "success_response": {...}}
        if isinstance(data, dict) and "success" in data:
            if not data.get("success"):
                error_resp = data.get("error_response", {})
                error_msg = error_resp.get("message", "Unknown order error")
                failure_reason = error_resp.get("new_order_failure_reason", "")
                raise OrderRejectedError(
                    400,
                    f"{error_msg} (reason: {failure_reason})",
                    "/api/v3/brokerage/orders",
                )
            order_data = data.get("success_response", data)
        else:
            order_data = data

        return OrderResponse.model_validate(order_data)

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order.

        Advanced Trade uses POST /orders/batch_cancel with a body
        instead of DELETE /orders/{id}.

        Args:
            order_id: Exchange order ID to cancel.
        """
        data = await self._request(
            "POST",
            "/api/v3/brokerage/orders/batch_cancel",
            body={"order_ids": [order_id]},
        )
        # Check for failure in results
        if isinstance(data, dict):
            results = data.get("results", [])
            for result in results:
                if not result.get("success", True):
                    raise CoinbaseAPIError(
                        400,
                        result.get("failure_reason", "Cancel failed"),
                        "/api/v3/brokerage/orders/batch_cancel",
                    )

    async def get_fills(
        self,
        product_id: str | None = None,
        order_id: str | None = None,
        limit: int = 100,
    ) -> list[FillResponse]:
        """Get fill history.

        Args:
            product_id: Filter by product (was instrument_id).
            order_id: Filter by order.
            limit: Maximum number of fills to return.
        """
        params: dict[str, Any] = {"limit": limit}
        if product_id:
            params["product_ids"] = product_id
        if order_id:
            params["order_ids"] = order_id
        data = await self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/fills",
            params=params,
        )
        items = data.get("fills", []) if isinstance(data, dict) else data
        return [FillResponse.model_validate(item) for item in items]
