"""Portfolio target registry.

On Coinbase Advanced, each API key is scoped to a single portfolio.
Portfolio routing is handled by CoinbaseClientPool.get_client(target),
which selects the correct API key. No portfolio UUIDs are needed.
"""

from __future__ import annotations

from libs.common.models.enums import PortfolioTarget

__all__ = ["PortfolioTarget"]
