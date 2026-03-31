"""Single-client Coinbase Advanced Trade REST client wrapper.

Wraps a single CoinbaseRESTClient and exposes the same interface as
the former dual-portfolio pool so downstream agents need minimal changes.
get_client(route) returns the same client regardless of the Route value.
"""

from __future__ import annotations

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.constants import DEFAULT_REST_BASE_URL
from libs.common.models.enums import Route


class CoinbaseClientPool:
    """Single-client wrapper that satisfies the pool interface.

    All routes resolve to the same underlying REST client authenticated
    with a single API key. Use this when operating with one portfolio.

    Args:
        auth: CoinbaseAuth for the API key.
        base_url: REST API base URL.
        portfolio_uuid: Portfolio UUID for portfolio-scoped endpoints.
    """

    def __init__(
        self,
        auth: CoinbaseAuth,
        base_url: str = DEFAULT_REST_BASE_URL,
        portfolio_uuid: str = "",
    ) -> None:
        self._client = CoinbaseRESTClient(
            auth=auth,
            base_url=base_url,
            rate_limiter=RateLimiter(),
            portfolio_uuid=portfolio_uuid,
        )

    def get_client(self, target: Route) -> CoinbaseRESTClient:
        """Get the REST client.

        Args:
            target: Ignored — all routes share the same client.

        Returns:
            The single CoinbaseRESTClient instance.
        """
        return self._client

    @property
    def market_client(self) -> CoinbaseRESTClient:
        """Client for non-portfolio-scoped endpoints (market data, funding)."""
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    async def __aenter__(self) -> CoinbaseClientPool:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
