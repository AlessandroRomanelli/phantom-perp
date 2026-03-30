"""Thin async HTTP client for the Coinglass v4 API.

Wraps ``httpx.AsyncClient`` to call Coinglass open-API endpoints.  All
non-2xx responses and API-level error codes (``code != "0"`` in the JSON
body) are raised as ``CoinglassAPIError``.

Usage::

    async with CoinglassClient(api_key="...") as client:
        raw = await client.get_liquidation_heatmap("ETH")
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from libs.common.exceptions import CoinglassAPIError

_logger = structlog.get_logger(__name__)

# Default API base URL for Coinglass Open API v4
_DEFAULT_BASE_URL: str = "https://open-api-v4.coinglass.com"

# Liquidation heatmap endpoint path
_HEATMAP_PATH: str = "/api/futures/liquidation/heatmap/model2"


class CoinglassClient:
    """Async REST client for Coinglass Open API v4.

    Args:
        api_key: Coinglass API key (``CG-API-KEY`` header).
        base_url: Base URL for the API.  Defaults to the production endpoint.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "CG-API-KEY": self._api_key,
                "Accept": "application/json",
            },
            timeout=self._timeout,
        )

    async def get_liquidation_heatmap(
        self,
        symbol: str,
        interval: str = "12h",
    ) -> dict[str, Any]:
        """Fetch the liquidation heatmap (model2) for a symbol.

        Args:
            symbol: Coin symbol, e.g. ``"ETH"`` or ``"BTC"``.
            interval: Time interval for the heatmap (e.g. ``"12h"``, ``"1d"``).

        Returns:
            The ``data`` dict from the Coinglass API response.

        Raises:
            CoinglassAPIError: On non-2xx HTTP status or API-level error code.
        """
        url = f"{_HEATMAP_PATH}?symbol={symbol}&interval={interval}"
        _logger.debug(
            "coinglass_request",
            symbol=symbol,
            interval=interval,
            url=url,
        )

        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise CoinglassAPIError(0, str(exc)) from exc

        if response.status_code != 200:
            raise CoinglassAPIError(
                response.status_code,
                f"HTTP {response.status_code} on {url}",
            )

        try:
            body: dict[str, Any] = response.json()
        except Exception as exc:
            raise CoinglassAPIError(response.status_code, f"Invalid JSON: {exc}") from exc

        # Coinglass wraps successful responses with code="0"
        api_code = str(body.get("code", ""))
        if api_code != "0":
            msg = body.get("msg") or body.get("message") or "unknown error"
            raise CoinglassAPIError(response.status_code, f"API code={api_code}: {msg}")

        data: dict[str, Any] = body.get("data", {})
        return data

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def __aenter__(self) -> "CoinglassClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
