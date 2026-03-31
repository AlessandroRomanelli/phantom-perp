"""Tests for the paper trading simulator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.models.enums import OrderSide, Route, PositionSide
from libs.common.models.order import Fill
from libs.common.utils import utc_now
from libs.storage.models import Base, FillRecord

from agents.reconciliation.paper_simulator import (
    PaperPortfolio,
    PendingProtectiveOrder,
    SimulatedPosition,
    _persist_fill,
)


@pytest.fixture
def portfolio_a() -> PaperPortfolio:
    return PaperPortfolio(
        target=Route.A,
        initial_equity=Decimal("10000"),
    )


class TestSimulatedPosition:
    def test_long_unrealized_profit(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.LONG,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("2100")) == Decimal("100")

    def test_long_unrealized_loss(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.LONG,
            size=Decimal("2.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("1950")) == Decimal("-100")

    def test_short_unrealized_profit(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.SHORT,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("1900")) == Decimal("100")

    def test_short_unrealized_loss(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.SHORT,
            size=Decimal("1.0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("2100")) == Decimal("-100")

    def test_flat_no_pnl(self) -> None:
        pos = SimulatedPosition(
            instrument="ETH-PERP",
            side=PositionSide.FLAT,
            size=Decimal("0"),
            entry_price=Decimal("2000"),
        )
        assert pos.unrealized_pnl(Decimal("3000")) == Decimal("0")


class TestPaperPortfolioFills:
    def test_open_long_position(self, portfolio_a: PaperPortfolio) -> None:
        fill = portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        assert fill.side == OrderSide.BUY
        assert fill.size == Decimal("1.0")
        assert fill.price == Decimal("2000")
        assert fill.is_maker is True
        assert fill.fee_usdc == (Decimal("2000") * FEE_MAKER).quantize(Decimal("0.01"))

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.LONG
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.entry_price == Decimal("2000")

    def test_open_short_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            fill_price=Decimal("2500"),
            is_maker=False,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.SHORT
        assert portfolio_a.position.size == Decimal("0.5")
        # Taker fee
        expected_fee = (Decimal("1250") * FEE_TAKER).quantize(Decimal("0.01"))
        assert portfolio_a.fees_paid == expected_fee

    def test_add_to_position_averages_entry(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.size == Decimal("2.0")
        assert portfolio_a.position.entry_price == Decimal("2100")  # avg

    def test_close_position_realizes_profit(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        assert portfolio_a.position is None
        assert portfolio_a.realized_pnl == Decimal("100")

    def test_close_position_realizes_loss(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("1900"),
            is_maker=True,
        )

        assert portfolio_a.position is None
        assert portfolio_a.realized_pnl == Decimal("-100")

    def test_partial_close(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("2.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2200"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.side == PositionSide.LONG
        assert portfolio_a.realized_pnl == Decimal("200")  # (2200-2000)*1

    def test_flip_position(self, portfolio_a: PaperPortfolio) -> None:
        # Open long 1.0
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        # Sell 2.0 — closes 1.0 long + opens 1.0 short
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("2.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        assert portfolio_a.position is not None
        assert portfolio_a.position.side == PositionSide.SHORT
        assert portfolio_a.position.size == Decimal("1.0")
        assert portfolio_a.position.entry_price == Decimal("2100")
        assert portfolio_a.realized_pnl == Decimal("100")  # from closing the long

    def test_fill_count_increments(self, portfolio_a: PaperPortfolio) -> None:
        for i in range(3):
            portfolio_a.apply_fill(
                order_id=f"ord-{i}",
                instrument="ETH-PERP",
                side=OrderSide.BUY,
                size=Decimal("0.1"),
                fill_price=Decimal("2000"),
                is_maker=True,
            )
        assert portfolio_a.fill_count == 3


class TestPaperPortfolioFunding:
    def test_no_position_no_funding(self, portfolio_a: PaperPortfolio) -> None:
        result = portfolio_a.apply_funding("ETH-PERP", Decimal("0.0001"), Decimal("2000"))
        assert result is None

    def test_long_pays_positive_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding("ETH-PERP", Decimal("0.0001"), Decimal("2000"))

        assert payment is not None
        # rate=0.0001 * notional=2000 = 0.20, long pays → -0.20
        assert payment.payment_usdc == Decimal("-0.20")
        assert payment.position_side == PositionSide.LONG
        assert portfolio_a.funding_pnl == Decimal("-0.20")

    def test_short_receives_positive_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding("ETH-PERP", Decimal("0.0001"), Decimal("2000"))

        assert payment is not None
        assert payment.payment_usdc == Decimal("0.20")
        assert payment.position_side == PositionSide.SHORT
        assert portfolio_a.funding_pnl == Decimal("0.20")

    def test_long_receives_negative_rate(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        payment = portfolio_a.apply_funding("ETH-PERP", Decimal("-0.0002"), Decimal("2000"))

        assert payment is not None
        # -rate * notional = -(-0.0002)*2000 = +0.40 for longs
        assert payment.payment_usdc == Decimal("0.40")
        assert portfolio_a.funding_pnl == Decimal("0.40")

    def test_cumulative_funding_on_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        portfolio_a.apply_funding("ETH-PERP", Decimal("0.0001"), Decimal("2000"))
        portfolio_a.apply_funding("ETH-PERP", Decimal("0.0001"), Decimal("2000"))

        assert portfolio_a.position is not None
        assert portfolio_a.position.cumulative_funding_usdc == Decimal("-0.40")


class TestPaperPortfolioSnapshot:
    def test_empty_portfolio_snapshot(self, portfolio_a: PaperPortfolio) -> None:
        snapshot = portfolio_a.build_snapshot({"ETH-PERP": Decimal("2000")})

        assert snapshot.equity_usdc == Decimal("10000.00")
        assert snapshot.used_margin_usdc == Decimal("0")
        assert snapshot.unrealized_pnl_usdc == Decimal("0.00")
        assert len(snapshot.positions) == 0
        assert len(snapshot.open_positions) == 0

    def test_snapshot_with_position(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot({"ETH-PERP": Decimal("2100")})

        assert len(snapshot.open_positions) == 1
        assert snapshot.unrealized_pnl_usdc == Decimal("100.00")
        assert snapshot.used_margin_usdc > Decimal("0")
        assert snapshot.margin_utilization_pct > 0

        pos = snapshot.positions[0]
        assert pos.side == PositionSide.LONG
        assert pos.size == Decimal("1.0")
        assert pos.entry_price == Decimal("2000")
        assert pos.mark_price == Decimal("2100")

    def test_equity_reflects_fees(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot({"ETH-PERP": Decimal("2000")})

        # Equity should be initial - fees (no unrealized P&L at same price)
        expected_fee = (Decimal("2000") * FEE_MAKER).quantize(Decimal("0.01"))
        assert snapshot.fees_paid_today_usdc == expected_fee
        # equity = 10000 - fee + unrealized(0)
        assert snapshot.equity_usdc == (Decimal("10000") - expected_fee).quantize(Decimal("0.01"))

    def test_equity_reflects_realized_pnl(self, portfolio_a: PaperPortfolio) -> None:
        portfolio_a.apply_fill(
            order_id="ord-1",
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            fill_price=Decimal("2000"),
            is_maker=True,
        )
        portfolio_a.apply_fill(
            order_id="ord-2",
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            fill_price=Decimal("2100"),
            is_maker=True,
        )

        snapshot = portfolio_a.build_snapshot({"ETH-PERP": Decimal("2100")})

        assert snapshot.realized_pnl_today_usdc == Decimal("100.00")
        assert snapshot.fees_paid_today_usdc > Decimal("0")
        # No position, so no unrealized P&L
        assert snapshot.unrealized_pnl_usdc == Decimal("0.00")

    def test_snapshot_portfolio_metadata(self, portfolio_a: PaperPortfolio) -> None:
        snapshot = portfolio_a.build_snapshot({"ETH-PERP": Decimal("2000")})

        assert snapshot.route == Route.A


# ---------------------------------------------------------------------------
# Helpers for _persist_fill tests
# ---------------------------------------------------------------------------

_T0 = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_fill(
    fill_id: str = "fill-test-1",
    order_id: str = "order-test-1",
    instrument: str = "ETH-PERP",
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("2000"),
    fee_usdc: Decimal = Decimal("0.25"),
    is_maker: bool = True,
    portfolio: Route = Route.A,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        route=portfolio,
        instrument=instrument,
        side=side,
        size=size,
        price=price,
        fee_usdc=fee_usdc,
        is_maker=is_maker,
        filled_at=_T0,
        trade_id=f"trade-{fill_id}",
    )


class _AsyncSessionWrapper:
    """Wraps sync SQLAlchemy Session to present async interface for tests."""

    def __init__(self, sync_session: Session) -> None:
        self._session = sync_session

    async def execute(self, stmt: object) -> object:
        return self._session.execute(stmt)  # type: ignore[arg-type]

    def add(self, obj: object) -> None:
        self._session.add(obj)

    async def commit(self) -> None:
        self._session.commit()


def _make_repo_with_sqlite() -> tuple[MagicMock, sessionmaker]:  # type: ignore[type-arg]
    """Create a mock RelationalStore backed by in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    store = MagicMock()

    @asynccontextmanager
    async def _session_ctx() -> AsyncIterator[_AsyncSessionWrapper]:
        sess = factory()
        try:
            yield _AsyncSessionWrapper(sess)
        finally:
            sess.close()

    store.session = _session_ctx
    return store, factory


# ---------------------------------------------------------------------------
# Tests: _persist_fill
# ---------------------------------------------------------------------------


class TestPersistFill:
    @pytest.mark.asyncio
    async def test_persist_fill_writes_to_db(self) -> None:
        """_persist_fill writes a FillRecord when repo is provided."""
        from libs.storage.repository import TunerRepository

        store, factory = _make_repo_with_sqlite()
        repo = TunerRepository(store)

        fill = _make_fill()
        await _persist_fill(repo, fill)

        with factory() as session:
            result = session.execute(
                select(FillRecord).where(FillRecord.fill_id == "fill-test-1")
            ).scalar_one_or_none()
            assert result is not None
            assert result.order_id == "order-test-1"
            assert result.instrument == "ETH-PERP"
            assert result.side == "BUY"
            assert result.size == Decimal("1.0")
            assert result.price == Decimal("2000")
            assert result.is_maker is True

    @pytest.mark.asyncio
    async def test_persist_fill_noop_when_repo_is_none(self) -> None:
        """_persist_fill does nothing when repo is None."""
        fill = _make_fill()
        # Should not raise
        await _persist_fill(None, fill)

    @pytest.mark.asyncio
    async def test_persist_fill_logs_on_db_error(self) -> None:
        """_persist_fill logs warning but does not raise on DB failure."""
        from libs.storage.repository import TunerRepository

        store = MagicMock()
        repo = TunerRepository(store)

        # Make session.commit() raise
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock(side_effect=Exception("DB down"))

        @asynccontextmanager
        async def _broken_session() -> AsyncIterator[AsyncMock]:
            yield mock_session

        store.session = _broken_session

        fill = _make_fill()
        # Should not raise — error is caught and logged
        await _persist_fill(repo, fill)

    @pytest.mark.asyncio
    async def test_persist_sl_exit_fill(self) -> None:
        """_persist_fill correctly persists a stop-loss exit fill."""
        from libs.storage.repository import TunerRepository

        store, factory = _make_repo_with_sqlite()
        repo = TunerRepository(store)

        # Simulate SL exit fill (opposite side SELL, reduce-only)
        sl_fill = _make_fill(
            fill_id="fill-sl-1",
            order_id="sl-order-1",
            side=OrderSide.SELL,
            price=Decimal("1900"),
        )
        await _persist_fill(repo, sl_fill)

        with factory() as session:
            result = session.execute(
                select(FillRecord).where(FillRecord.fill_id == "fill-sl-1")
            ).scalar_one_or_none()
            assert result is not None
            assert result.order_id == "sl-order-1"
            assert result.side == "SELL"
            assert result.price == Decimal("1900")

    @pytest.mark.asyncio
    async def test_persist_tp_exit_fill(self) -> None:
        """_persist_fill correctly persists a take-profit exit fill."""
        from libs.storage.repository import TunerRepository

        store, factory = _make_repo_with_sqlite()
        repo = TunerRepository(store)

        tp_fill = _make_fill(
            fill_id="fill-tp-1",
            order_id="tp-order-1",
            side=OrderSide.SELL,
            price=Decimal("2200"),
            is_maker=False,
        )
        await _persist_fill(repo, tp_fill)

        with factory() as session:
            result = session.execute(
                select(FillRecord).where(FillRecord.fill_id == "fill-tp-1")
            ).scalar_one_or_none()
            assert result is not None
            assert result.order_id == "tp-order-1"
            assert result.side == "SELL"
            assert result.price == Decimal("2200")
            assert result.is_maker is False

    @pytest.mark.asyncio
    async def test_entry_and_exit_fills_both_persisted(self) -> None:
        """Both entry and exit (SL/TP) fills are persisted to the same table."""
        from libs.storage.repository import TunerRepository

        store, factory = _make_repo_with_sqlite()
        repo = TunerRepository(store)

        entry_fill = _make_fill(fill_id="fill-entry", order_id="order-1")
        sl_fill = _make_fill(
            fill_id="fill-sl-exit",
            order_id="sl-order-1",
            side=OrderSide.SELL,
            price=Decimal("1900"),
        )

        await _persist_fill(repo, entry_fill)
        await _persist_fill(repo, sl_fill)

        with factory() as session:
            all_fills = session.execute(select(FillRecord)).scalars().all()
            assert len(all_fills) == 2
            fill_ids = {f.fill_id for f in all_fills}
            assert fill_ids == {"fill-entry", "fill-sl-exit"}


# ---------------------------------------------------------------------------
# Tests: PendingProtectiveOrder triggering
# ---------------------------------------------------------------------------


class TestPendingProtectiveOrderTrigger:
    def test_sl_long_triggers_on_price_drop(self) -> None:
        """SL for a LONG (close side = SELL) triggers when mark <= trigger."""
        order = PendingProtectiveOrder(
            order_id="sl-1",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            trigger_price=Decimal("1900"),
            fill_price=Decimal("1900"),
            is_stop_loss=True,
        )
        assert order.is_triggered(Decimal("1900")) is True
        assert order.is_triggered(Decimal("1850")) is True
        assert order.is_triggered(Decimal("1950")) is False

    def test_sl_short_triggers_on_price_rise(self) -> None:
        """SL for a SHORT (close side = BUY) triggers when mark >= trigger."""
        order = PendingProtectiveOrder(
            order_id="sl-2",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            trigger_price=Decimal("2100"),
            fill_price=Decimal("2100"),
            is_stop_loss=True,
        )
        assert order.is_triggered(Decimal("2100")) is True
        assert order.is_triggered(Decimal("2200")) is True
        assert order.is_triggered(Decimal("2050")) is False

    def test_tp_long_triggers_on_price_rise(self) -> None:
        """TP for a LONG (close side = SELL) triggers when mark >= trigger."""
        order = PendingProtectiveOrder(
            order_id="tp-1",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.SELL,
            size=Decimal("1.0"),
            trigger_price=Decimal("2200"),
            fill_price=Decimal("2200"),
            is_stop_loss=False,
        )
        assert order.is_triggered(Decimal("2200")) is True
        assert order.is_triggered(Decimal("2300")) is True
        assert order.is_triggered(Decimal("2100")) is False

    def test_tp_short_triggers_on_price_drop(self) -> None:
        """TP for a SHORT (close side = BUY) triggers when mark <= trigger."""
        order = PendingProtectiveOrder(
            order_id="tp-2",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            trigger_price=Decimal("1800"),
            fill_price=Decimal("1800"),
            is_stop_loss=False,
        )
        assert order.is_triggered(Decimal("1800")) is True
        assert order.is_triggered(Decimal("1700")) is True
        assert order.is_triggered(Decimal("1900")) is False
