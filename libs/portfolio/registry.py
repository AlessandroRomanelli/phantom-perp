"""Route registry.

On Coinbase Advanced, each API key is scoped to a single portfolio.
Route routing is handled by CoinbaseClientPool.get_client(route),
which selects the correct API key. No portfolio UUIDs are needed.
"""

from __future__ import annotations

from libs.common.models.enums import Route

__all__ = ["Route"]
