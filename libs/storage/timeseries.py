"""TimescaleDB adapter for time-series data (candles, hourly funding, P&L)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


class TimeseriesStore:
    """Async interface to TimescaleDB for time-series storage.

    Stores candle data, hourly funding rates, and P&L snapshots.

    Args:
        database_url: Async PostgreSQL connection URL.
    """

    def __init__(self, database_url: str) -> None:
        # Convert postgresql:// to postgresql+asyncpg://
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self._engine = create_async_engine(database_url, echo=False)

    async def insert_candle(
        self,
        instrument: str,
        timestamp: datetime,
        open_: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: Decimal,
    ) -> None:
        """Insert a single OHLCV candle."""
        raise NotImplementedError("Schema migration required first")

    async def insert_funding_rate(
        self,
        instrument: str,
        timestamp: datetime,
        rate: Decimal,
        mark_price: Decimal,
    ) -> None:
        """Insert an hourly funding rate record."""
        raise NotImplementedError("Schema migration required first")

    async def query_candles(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        granularity: str = "1h",
    ) -> list[dict[str, Any]]:
        """Query candle data for a time range."""
        raise NotImplementedError("Schema migration required first")

    async def close(self) -> None:
        """Dispose of the engine connection pool."""
        await self._engine.dispose()
