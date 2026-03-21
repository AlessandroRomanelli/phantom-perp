"""Shared mutable state for the ingestion agent.

All data sources (WS, REST candles, REST funding) write into this single
object. The normalizer reads from it to build MarketSnapshot instances.

Since everything runs on a single asyncio event loop, no locks are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from libs.coinbase.models import CandleResponse


@dataclass
class BookLevel:
    """Single price level in the order book."""

    price: Decimal
    size: Decimal


@dataclass
class IngestionState:
    """Mutable state shared across all ingestion data sources.

    Updated by:
      - ws_market_data: best_bid, best_ask, last_price, mark_price, index_price,
                        volume_24h, open_interest, bid_depth, ask_depth
      - candles: candles_by_granularity
      - funding_rate: funding_rate, next_funding_time, funding_mark_price
    """

    # ── WebSocket-sourced fields ────────────────────────────────────────

    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    last_price: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    volume_24h: Decimal | None = None
    open_interest: Decimal | None = None

    bid_depth: list[BookLevel] = field(default_factory=list)
    ask_depth: list[BookLevel] = field(default_factory=list)

    last_ws_update: datetime | None = None

    # ── REST candle-sourced fields ──────────────────────────────────────

    candles_by_granularity: dict[str, list[CandleResponse]] = field(default_factory=dict)

    # ── REST funding-sourced fields ─────────────────────────────────────

    funding_rate: Decimal | None = None
    next_funding_time: datetime | None = None
    funding_mark_price: Decimal | None = None
    funding_index_price: Decimal | None = None
    last_funding_update: datetime | None = None

    def has_minimum_data(self) -> bool:
        """Check if we have enough data to build a MarketSnapshot.

        mark_price can come from WS or from the funding REST endpoint.
        index_price can fall back to last_price (spot approximation).
        """
        mark = self.mark_price or self.funding_mark_price
        index = self.index_price or self.last_price
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and self.last_price is not None
            and mark is not None
            and index is not None
        )
