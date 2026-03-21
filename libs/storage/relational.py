"""PostgreSQL relational store for orders, trades, and configuration."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class RelationalStore:
    """Async interface to PostgreSQL for relational data.

    Stores orders, trades, fill history, and agent configuration.

    Args:
        database_url: Async PostgreSQL connection URL.
    """

    def __init__(self, database_url: str) -> None:
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self._engine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def get_session(self) -> AsyncSession:
        """Create a new async session."""
        return self._session_factory()

    async def close(self) -> None:
        """Dispose of the engine connection pool."""
        await self._engine.dispose()
