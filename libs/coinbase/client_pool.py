"""Per-portfolio Coinbase Advanced Trade REST client pool.

Coinbase Advanced Trade API keys are portfolio-scoped: each key is
created under a specific portfolio and can only operate on that
portfolio. Portfolio-scoped endpoints also require a portfolio_uuid
in the request path. This module provides a pool that routes API calls
to the correct client based on the target portfolio.
"""

from __future__ import annotations

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.constants import DEFAULT_REST_BASE_URL
from libs.common.models.enums import PortfolioTarget


class CoinbaseClientPool:
    """Routes API calls to the per-portfolio REST client.

    Each portfolio has its own API key (and therefore its own auth,
    HTTP client, and rate limiter). Portfolio-scoped endpoints use
    the portfolio_uuid injected at client construction time.

    Non-portfolio-scoped endpoints (market data, products, funding)
    can use either client -- by convention we use Portfolio A's.

    Args:
        auth_a: CoinbaseAuth for Portfolio A's API key.
        auth_b: CoinbaseAuth for Portfolio B's API key.
        base_url: REST API base URL (shared).
        portfolio_uuid_a: Portfolio UUID for Portfolio A.
        portfolio_uuid_b: Portfolio UUID for Portfolio B.
    """

    def __init__(
        self,
        auth_a: CoinbaseAuth,
        auth_b: CoinbaseAuth,
        base_url: str = DEFAULT_REST_BASE_URL,
        portfolio_uuid_a: str = "",
        portfolio_uuid_b: str = "",
    ) -> None:
        self._client_a = CoinbaseRESTClient(
            auth=auth_a,
            base_url=base_url,
            rate_limiter=RateLimiter(),
            portfolio_uuid=portfolio_uuid_a,
        )
        self._client_b = CoinbaseRESTClient(
            auth=auth_b,
            base_url=base_url,
            rate_limiter=RateLimiter(),
            portfolio_uuid=portfolio_uuid_b,
        )
        self._clients = {
            PortfolioTarget.A: self._client_a,
            PortfolioTarget.B: self._client_b,
        }

    def get_client(self, target: PortfolioTarget) -> CoinbaseRESTClient:
        """Get the REST client for a specific portfolio.

        Args:
            target: Portfolio A or B.

        Returns:
            The CoinbaseRESTClient authenticated with that portfolio's API key.
        """
        return self._clients[target]

    @property
    def market_client(self) -> CoinbaseRESTClient:
        """Client for non-portfolio-scoped endpoints (market data, funding).

        Uses Portfolio A's client by convention. Public endpoints like
        products, orderbook, candles, and funding rate are not
        portfolio-scoped and work with any valid API key.
        """
        return self._client_a

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        await self._client_a.close()
        await self._client_b.close()

    async def __aenter__(self) -> CoinbaseClientPool:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
