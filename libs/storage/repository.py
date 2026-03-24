"""Repository query layer for the AI tuner's PostgreSQL data pipeline.

Provides typed query methods that aggregate fill, order signal, and signal data
for the tuner's performance analysis. All queries use the RelationalStore session()
context manager for connection lifecycle management.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from libs.storage.models import FillRecord, OrderSignalRecord, SignalRecord
from libs.storage.relational import RelationalStore


@dataclass(frozen=True, slots=True)
class AttributedFill:
    """A fill record with signal attribution populated from the order_signals JOIN.

    The attribution chain is:
        fills.order_id -> order_signals.order_id -> primary_source (SignalSource.value)

    This satisfies DATA-04: signal source attribution is preserved through the
    order-to-fill chain in PostgreSQL.
    """

    fill_id: str
    order_id: str
    portfolio_target: str
    instrument: str
    side: str
    size: Decimal
    price: Decimal
    fee_usdc: Decimal
    is_maker: bool
    filled_at: datetime
    trade_id: str
    primary_source: str  # from order_signals JOIN — SignalSource.value
    conviction: float  # from order_signals JOIN


class TunerRepository:
    """Typed query interface for the AI tuner's performance analysis.

    Wraps RelationalStore with methods returning strongly-typed result objects.
    All methods use rolling time window queries (default: 30 days).

    Args:
        store: RelationalStore instance providing session() context manager.
    """

    def __init__(self, store: RelationalStore) -> None:
        self._store = store

    async def get_fills_by_strategy(
        self, portfolio_target: str = "autonomous", days: int = 30
    ) -> list[AttributedFill]:
        """Query fills with strategy attribution, filtered by portfolio and time window.

        Returns fills INNER JOINed with order_signals — only fills with attribution
        are returned. This satisfies DATA-01 and DATA-04.

        Args:
            portfolio_target: PortfolioTarget.value to filter by (e.g. "autonomous").
            days: Rolling lookback window in days.

        Returns:
            List of AttributedFill sorted by filled_at ascending.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(FillRecord, OrderSignalRecord.primary_source, OrderSignalRecord.conviction)
            .join(OrderSignalRecord, FillRecord.order_id == OrderSignalRecord.order_id)
            .where(FillRecord.portfolio_target == portfolio_target)
            .where(FillRecord.filled_at >= cutoff)
            .order_by(FillRecord.filled_at)
        )
        async with self._store.session() as session:
            rows = (await session.execute(stmt)).all()  # type: ignore[union-attr]
        return [
            AttributedFill(
                fill_id=row.FillRecord.fill_id,
                order_id=row.FillRecord.order_id or "",
                portfolio_target=row.FillRecord.portfolio_target,
                instrument=row.FillRecord.instrument,
                side=row.FillRecord.side,
                size=row.FillRecord.size,
                price=row.FillRecord.price,
                fee_usdc=row.FillRecord.fee_usdc,
                is_maker=row.FillRecord.is_maker,
                filled_at=row.FillRecord.filled_at,
                trade_id=row.FillRecord.trade_id,
                primary_source=row.primary_source,
                conviction=row.conviction,
            )
            for row in rows
        ]

    async def get_fills_by_instrument(
        self, portfolio_target: str = "autonomous", days: int = 30
    ) -> list[AttributedFill]:
        """Query fills ordered by instrument for per-instrument performance analysis.

        Same attribution JOIN as get_fills_by_strategy(), but ordered by instrument
        then filled_at. Satisfies DATA-03.

        Args:
            portfolio_target: PortfolioTarget.value to filter by.
            days: Rolling lookback window in days.

        Returns:
            List of AttributedFill sorted by instrument, then filled_at.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(FillRecord, OrderSignalRecord.primary_source, OrderSignalRecord.conviction)
            .join(OrderSignalRecord, FillRecord.order_id == OrderSignalRecord.order_id)
            .where(FillRecord.portfolio_target == portfolio_target)
            .where(FillRecord.filled_at >= cutoff)
            .order_by(FillRecord.instrument, FillRecord.filled_at)
        )
        async with self._store.session() as session:
            rows = (await session.execute(stmt)).all()  # type: ignore[union-attr]
        return [
            AttributedFill(
                fill_id=row.FillRecord.fill_id,
                order_id=row.FillRecord.order_id or "",
                portfolio_target=row.FillRecord.portfolio_target,
                instrument=row.FillRecord.instrument,
                side=row.FillRecord.side,
                size=row.FillRecord.size,
                price=row.FillRecord.price,
                fee_usdc=row.FillRecord.fee_usdc,
                is_maker=row.FillRecord.is_maker,
                filled_at=row.FillRecord.filled_at,
                trade_id=row.FillRecord.trade_id,
                primary_source=row.primary_source,
                conviction=row.conviction,
            )
            for row in rows
        ]

    async def get_order_signals(
        self, portfolio_target: str = "autonomous", days: int = 30
    ) -> list[OrderSignalRecord]:
        """Query order signal records for order lifecycle analysis.

        Returns raw ORM objects for rejection rate and slippage analysis (D-05 item 2).

        Args:
            portfolio_target: PortfolioTarget.value to filter by.
            days: Rolling lookback window in days.

        Returns:
            List of OrderSignalRecord sorted by proposed_at ascending.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(OrderSignalRecord)
            .where(OrderSignalRecord.portfolio_target == portfolio_target)
            .where(OrderSignalRecord.proposed_at >= cutoff)
            .order_by(OrderSignalRecord.proposed_at)
        )
        async with self._store.session() as session:
            rows = (await session.execute(stmt)).scalars().all()  # type: ignore[union-attr]
        return list(rows)

    async def get_signals(
        self, instrument: str | None = None, days: int = 30
    ) -> list[SignalRecord]:
        """Query signal records for signal metadata analysis.

        Returns raw ORM objects for conviction-outcome correlation (D-05 item 3).

        Args:
            instrument: Optional instrument filter (e.g. "ETH-PERP-INTX").
            days: Rolling lookback window in days.

        Returns:
            List of SignalRecord sorted by timestamp ascending.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = select(SignalRecord).where(SignalRecord.timestamp >= cutoff)
        if instrument is not None:
            stmt = stmt.where(SignalRecord.instrument == instrument)
        stmt = stmt.order_by(SignalRecord.timestamp)
        async with self._store.session() as session:
            rows = (await session.execute(stmt)).scalars().all()  # type: ignore[union-attr]
        return list(rows)

    async def write_fill(self, record: FillRecord) -> None:
        """Persist a fill record to the database.

        Args:
            record: FillRecord ORM instance to persist.
        """
        async with self._store.session() as session:
            session.add(record)
            await session.commit()  # type: ignore[union-attr]

    async def write_order_signal(self, record: OrderSignalRecord) -> None:
        """Persist an order signal record to the database.

        Args:
            record: OrderSignalRecord ORM instance to persist.
        """
        async with self._store.session() as session:
            session.add(record)
            await session.commit()  # type: ignore[union-attr]

    async def write_signal(self, record: SignalRecord) -> None:
        """Persist a signal record to the database.

        Args:
            record: SignalRecord ORM instance to persist.
        """
        async with self._store.session() as session:
            session.add(record)
            await session.commit()  # type: ignore[union-attr]
