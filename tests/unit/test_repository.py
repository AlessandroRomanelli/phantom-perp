"""Unit tests for TunerRepository in libs/storage/repository.py.

Uses an in-memory SQLite sync engine wrapped in async context managers.
This avoids requiring aiosqlite while still testing the actual query logic.

Coverage:
- DATA-01: get_fills_by_strategy() filters by route and time window
- DATA-02: Per-strategy grouping across multiple instruments
- DATA-03: get_fills_by_instrument() returns fills ordered by instrument
- DATA-04: Attribution JOIN path: fills.order_id -> order_signals.order_id -> primary_source
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from libs.storage.models import Base, FillRecord, OrderSignalRecord, SignalRecord


# ---------------------------------------------------------------------------
# Fixtures: synchronous SQLite in-memory engine for unit testing
# ---------------------------------------------------------------------------


def _make_sync_engine() -> object:
    """Create a synchronous in-memory SQLite engine and create all tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


class _AsyncSessionWrapper:
    """Wraps a synchronous SQLAlchemy Session to present an async interface.

    The repository code uses `await session.execute(stmt)` and `await session.commit()`
    which requires an async session. Since we use a sync SQLite engine for unit tests
    (no aiosqlite), this wrapper makes those calls awaitable via async coroutines.
    """

    def __init__(self, sync_session: Session) -> None:
        self._session = sync_session

    async def execute(self, stmt: object) -> object:
        """Async-compatible execute delegating to sync session."""
        return self._session.execute(stmt)  # type: ignore[arg-type]

    def add(self, obj: object) -> None:
        """Delegate add to sync session (synchronous, no await needed)."""
        self._session.add(obj)

    async def commit(self) -> None:
        """Async-compatible commit delegating to sync session."""
        self._session.commit()


def _make_store_mock(session: Session) -> MagicMock:
    """Create a mock RelationalStore whose session() yields the given SQLite session."""
    store = MagicMock()
    async_wrapper = _AsyncSessionWrapper(session)

    @asynccontextmanager
    async def _session_ctx() -> AsyncIterator[_AsyncSessionWrapper]:
        yield async_wrapper

    store.session = _session_ctx
    return store


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)
_YESTERDAY = _NOW - timedelta(days=1)
_LAST_WEEK = _NOW - timedelta(days=7)
_TWO_MONTHS_AGO = _NOW - timedelta(days=62)


def _make_signal_record(
    signal_id: str,
    instrument: str,
    source: str,
    ts: datetime | None = None,
) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        timestamp=ts or _YESTERDAY,
        instrument=instrument,
        source=source,
        direction="LONG",
        conviction=0.80,
        time_horizon_seconds=3600,
        reasoning="test signal",
        entry_price=None,
    )


def _make_order_signal(
    order_id: str,
    signal_id: str,
    instrument: str,
    primary_source: str,
    route: str = "autonomous",
    proposed_at: datetime | None = None,
) -> OrderSignalRecord:
    return OrderSignalRecord(
        order_id=order_id,
        signal_id=signal_id,
        portfolio_target=route,
        instrument=instrument,
        conviction=0.82,
        primary_source=primary_source,
        all_sources=primary_source,
        stop_loss=None,
        take_profit=None,
        limit_price=None,
        leverage=Decimal("2.0"),
        proposed_at=proposed_at or _YESTERDAY,
        reasoning="",
    )


def _make_fill(
    fill_id: str,
    order_id: str | None,
    instrument: str,
    route: str = "autonomous",
    filled_at: datetime | None = None,
) -> FillRecord:
    return FillRecord(
        fill_id=fill_id,
        order_id=order_id,
        portfolio_target=route,
        instrument=instrument,
        side="BUY",
        size=Decimal("1.0"),
        price=Decimal("2200.00"),
        fee_usdc=Decimal("0.50"),
        is_maker=False,
        filled_at=filled_at or _YESTERDAY,
        trade_id=f"trade-{fill_id}",
    )


# ---------------------------------------------------------------------------
# Tests: DATA-01 — get_fills_by_strategy() filters by route + time window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fills_by_strategy_portfolio_filter() -> None:
    """DATA-01: get_fills_by_strategy() returns only 'autonomous' fills."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        # Insert: 2 autonomous fills, 1 user_confirmed fill, each with matching order_signal
        session.add(_make_order_signal("order-a1", "sig-a1", "ETH-PERP-INTX", "momentum"))
        session.add(_make_order_signal("order-a2", "sig-a2", "BTC-PERP-INTX", "mean_reversion"))
        session.add(
            _make_order_signal(
                "order-b1",
                "sig-b1",
                "ETH-PERP-INTX",
                "momentum",
                route="user_confirmed",
            )
        )
        session.add(_make_fill("fill-a1", "order-a1", "ETH-PERP-INTX"))
        session.add(_make_fill("fill-a2", "order-a2", "BTC-PERP-INTX"))
        session.add(
            _make_fill(
                "fill-b1", "order-b1", "ETH-PERP-INTX", route="user_confirmed"
            )
        )
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    assert len(results) == 2
    fill_ids = {r.fill_id for r in results}
    assert "fill-a1" in fill_ids
    assert "fill-a2" in fill_ids
    assert "fill-b1" not in fill_ids
    # All results are for the correct portfolio
    for r in results:
        assert r.portfolio_target == "autonomous"


@pytest.mark.asyncio
async def test_fills_by_strategy_time_window_filter() -> None:
    """DATA-01: get_fills_by_strategy() excludes fills outside the time window."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(
            _make_order_signal("order-old", "sig-old", "ETH-PERP-INTX", "momentum", proposed_at=_TWO_MONTHS_AGO)
        )
        session.add(
            _make_order_signal("order-new", "sig-new", "ETH-PERP-INTX", "momentum", proposed_at=_YESTERDAY)
        )
        session.add(_make_fill("fill-old", "order-old", "ETH-PERP-INTX", filled_at=_TWO_MONTHS_AGO))
        session.add(_make_fill("fill-new", "order-new", "ETH-PERP-INTX", filled_at=_YESTERDAY))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    assert len(results) == 1
    assert results[0].fill_id == "fill-new"


# ---------------------------------------------------------------------------
# Tests: DATA-02 — per-strategy grouping across all 5 instruments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fills_by_strategy_multiple_instruments() -> None:
    """DATA-02: get_fills_by_strategy() returns fills across multiple instruments."""
    from libs.storage.repository import TunerRepository

    instruments = ["ETH-PERP-INTX", "BTC-PERP-INTX", "SOL-PERP-INTX", "QQQ-PERP-INTX", "SPY-PERP-INTX"]
    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        for i, inst in enumerate(instruments):
            session.add(
                _make_order_signal(f"order-{i}", f"sig-{i}", inst, "momentum")
            )
            session.add(_make_fill(f"fill-{i}", f"order-{i}", inst))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    assert len(results) == 5
    result_instruments = {r.instrument for r in results}
    assert result_instruments == set(instruments)
    # All attributed to momentum
    for r in results:
        assert r.primary_source == "momentum"


# ---------------------------------------------------------------------------
# Tests: DATA-04 — attribution JOIN path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_join() -> None:
    """DATA-04: primary_source is populated from order_signals JOIN."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(
            _make_order_signal("order-1", "sig-1", "ETH-PERP-INTX", "momentum")
        )
        session.add(
            _make_order_signal("order-2", "sig-2", "BTC-PERP-INTX", "funding_arb")
        )
        session.add(_make_fill("fill-1", "order-1", "ETH-PERP-INTX"))
        session.add(_make_fill("fill-2", "order-2", "BTC-PERP-INTX"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    # Map fill_id -> primary_source
    by_fill = {r.fill_id: r.primary_source for r in results}
    assert by_fill["fill-1"] == "momentum"
    assert by_fill["fill-2"] == "funding_arb"


@pytest.mark.asyncio
async def test_attribution_join_excludes_unattributed_fills() -> None:
    """DATA-04: Fills with no matching order_signals row are excluded (INNER JOIN)."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(
            _make_order_signal("order-1", "sig-1", "ETH-PERP-INTX", "momentum")
        )
        # fill-1 has a matching order_signal; fill-orphan has no matching row
        session.add(_make_fill("fill-1", "order-1", "ETH-PERP-INTX"))
        session.add(_make_fill("fill-orphan", "order-missing", "BTC-PERP-INTX"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    assert len(results) == 1
    assert results[0].fill_id == "fill-1"


@pytest.mark.asyncio
async def test_attribution_join_conviction_populated() -> None:
    """Attribution JOIN also populates conviction from order_signals."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(
            _make_order_signal("order-1", "sig-1", "ETH-PERP-INTX", "regime_trend")
        )
        session.add(_make_fill("fill-1", "order-1", "ETH-PERP-INTX"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    assert len(results) == 1
    assert results[0].conviction == pytest.approx(0.82)
    assert results[0].primary_source == "regime_trend"


# ---------------------------------------------------------------------------
# Tests: DATA-03 — get_fills_by_instrument()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fills_by_instrument_ordering() -> None:
    """DATA-03: get_fills_by_instrument() returns fills ordered by instrument."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)
    instruments = ["SOL-PERP-INTX", "BTC-PERP-INTX", "ETH-PERP-INTX"]

    with session_factory() as session:
        for i, inst in enumerate(instruments):
            session.add(_make_order_signal(f"order-{i}", f"sig-{i}", inst, "momentum"))
            session.add(_make_fill(f"fill-{i}", f"order-{i}", inst))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_instrument(portfolio_target="autonomous", days=30)

    assert len(results) == 3
    # Should be ordered by instrument alphabetically
    result_instruments = [r.instrument for r in results]
    assert result_instruments == sorted(result_instruments)


# ---------------------------------------------------------------------------
# Tests: get_order_signals() and get_signals()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_order_signals_returns_rows() -> None:
    """get_order_signals() returns OrderSignalRecord rows for a time window."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(_make_order_signal("order-1", "sig-1", "ETH-PERP-INTX", "momentum"))
        session.add(_make_order_signal("order-2", "sig-2", "BTC-PERP-INTX", "funding_arb"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_order_signals(portfolio_target="autonomous", days=30)

    assert len(results) == 2
    order_ids = {r.order_id for r in results}
    assert "order-1" in order_ids
    assert "order-2" in order_ids


@pytest.mark.asyncio
async def test_get_signals_returns_rows() -> None:
    """get_signals() returns SignalRecord rows for a time window."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(_make_signal_record("sig-1", "ETH-PERP-INTX", "momentum"))
        session.add(_make_signal_record("sig-2", "BTC-PERP-INTX", "mean_reversion"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_signals(days=30)

    assert len(results) == 2


@pytest.mark.asyncio
async def test_get_signals_instrument_filter() -> None:
    """get_signals() filters by instrument when provided."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(_make_signal_record("sig-1", "ETH-PERP-INTX", "momentum"))
        session.add(_make_signal_record("sig-2", "BTC-PERP-INTX", "momentum"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_signals(instrument="ETH-PERP-INTX", days=30)

    assert len(results) == 1
    assert results[0].signal_id == "sig-1"


# ---------------------------------------------------------------------------
# Tests: Write methods persist and can be read back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_fill_persists() -> None:
    """write_fill() persists a FillRecord that can be read back."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    fill = _make_fill("fill-write-1", "order-write-1", "ETH-PERP-INTX")
    order_signal = _make_order_signal("order-write-1", "sig-write-1", "ETH-PERP-INTX", "momentum")

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        await repo.write_fill(fill)
        await repo.write_order_signal(order_signal)

    with session_factory() as session:
        from sqlalchemy import select
        from libs.storage.models import FillRecord
        result = session.execute(select(FillRecord).where(FillRecord.fill_id == "fill-write-1")).scalar_one_or_none()
        assert result is not None
        assert result.instrument == "ETH-PERP-INTX"


@pytest.mark.asyncio
async def test_write_order_signal_persists() -> None:
    """write_order_signal() persists an OrderSignalRecord."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)
    order_signal = _make_order_signal("order-ws-1", "sig-ws-1", "SOL-PERP-INTX", "regime_trend")

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        await repo.write_order_signal(order_signal)

    with session_factory() as session:
        from sqlalchemy import select
        result = session.execute(
            select(OrderSignalRecord).where(OrderSignalRecord.order_id == "order-ws-1")
        ).scalar_one_or_none()
        assert result is not None
        assert result.primary_source == "regime_trend"


@pytest.mark.asyncio
async def test_write_signal_persists() -> None:
    """write_signal() persists a SignalRecord."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)
    signal = _make_signal_record("sig-wsig-1", "QQQ-PERP-INTX", "vwap")

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        await repo.write_signal(signal)

    with session_factory() as session:
        from sqlalchemy import select
        result = session.execute(
            select(SignalRecord).where(SignalRecord.signal_id == "sig-wsig-1")
        ).scalar_one_or_none()
        assert result is not None
        assert result.source == "vwap"


# ---------------------------------------------------------------------------
# Tests: per-strategy query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_strategy_query_groups_by_source() -> None:
    """get_fills_by_strategy() enables grouping by primary_source."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        # 3 momentum fills, 2 funding_arb fills
        for i in range(3):
            session.add(_make_order_signal(f"order-m{i}", f"sig-m{i}", "ETH-PERP-INTX", "momentum"))
            session.add(_make_fill(f"fill-m{i}", f"order-m{i}", "ETH-PERP-INTX"))
        for i in range(2):
            session.add(_make_order_signal(f"order-f{i}", f"sig-f{i}", "ETH-PERP-INTX", "funding_arb"))
            session.add(_make_fill(f"fill-f{i}", f"order-f{i}", "ETH-PERP-INTX"))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_strategy(portfolio_target="autonomous", days=30)

    by_source: dict[str, list] = {}
    for r in results:
        by_source.setdefault(r.primary_source, []).append(r)

    assert len(by_source["momentum"]) == 3
    assert len(by_source["funding_arb"]) == 2


@pytest.mark.asyncio
async def test_per_instrument_query() -> None:
    """get_fills_by_instrument() returns fills for per-instrument analysis."""
    from libs.storage.repository import TunerRepository

    engine = _make_sync_engine()
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        for i, inst in enumerate(["BTC-PERP-INTX", "ETH-PERP-INTX"]):
            for j in range(2):
                session.add(_make_order_signal(f"order-{i}-{j}", f"sig-{i}-{j}", inst, "momentum"))
                session.add(_make_fill(f"fill-{i}-{j}", f"order-{i}-{j}", inst))
        session.commit()

    with session_factory() as session:
        store = _make_store_mock(session)
        repo = TunerRepository(store)
        results = await repo.get_fills_by_instrument(portfolio_target="autonomous", days=30)

    assert len(results) == 4
    # First two should be BTC (alphabetically)
    btc_fills = [r for r in results if r.instrument == "BTC-PERP-INTX"]
    eth_fills = [r for r in results if r.instrument == "ETH-PERP-INTX"]
    assert len(btc_fills) == 2
    assert len(eth_fills) == 2
