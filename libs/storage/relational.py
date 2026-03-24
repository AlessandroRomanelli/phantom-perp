"""PostgreSQL relational store for orders, trades, and configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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

    @property
    def engine(self) -> AsyncEngine:
        """The underlying async SQLAlchemy engine."""
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Async context manager that yields a session and auto-closes it.

        Preferred over get_session() for all new code — handles cleanup automatically.

        Yields:
            AsyncSession: An active database session.
        """
        sess = self._session_factory()
        try:
            yield sess
        finally:
            await sess.close()

    async def get_session(self) -> AsyncSession:
        """Create a new async session.

        Deprecated: Use session() context manager instead to avoid connection leaks.
        Kept for backward compatibility.
        """
        return self._session_factory()

    async def close(self) -> None:
        """Dispose of the engine connection pool."""
        await self._engine.dispose()


async def init_db(engine: AsyncEngine) -> None:
    """Idempotent schema bootstrap — safe to call at every agent startup.

    Creates all ORM-defined tables if they do not exist. Uses engine.begin()
    to auto-commit the DDL transaction (required for PostgreSQL).

    Args:
        engine: The async SQLAlchemy engine to use for DDL.
    """
    from libs.storage.models import Base

    # engine.begin() auto-commits the DDL — engine.connect() does not
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
