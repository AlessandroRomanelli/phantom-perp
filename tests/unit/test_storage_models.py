"""Unit tests for ORM models in libs/storage/models.py."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest


class TestFillRecord:
    """Test FillRecord ORM model instantiation and field types."""

    def test_fill_record_instantiation(self) -> None:
        """FillRecord can be instantiated with all required fields."""
        from libs.storage.models import FillRecord

        now = datetime.now(timezone.utc)
        record = FillRecord(
            fill_id="fill-001",
            order_id="order-001",
            portfolio_target="autonomous",
            instrument="ETH-PERP-INTX",
            side="BUY",
            size=Decimal("1.5"),
            price=Decimal("2230.50"),
            fee_usdc=Decimal("0.55"),
            is_maker=False,
            filled_at=now,
            trade_id="trade-001",
        )
        assert record.fill_id == "fill-001"
        assert record.order_id == "order-001"
        assert record.portfolio_target == "autonomous"
        assert record.instrument == "ETH-PERP-INTX"
        assert record.side == "BUY"
        assert record.size == Decimal("1.5")
        assert record.price == Decimal("2230.50")
        assert record.fee_usdc == Decimal("0.55")
        assert record.is_maker is False
        assert record.filled_at == now
        assert record.trade_id == "trade-001"

    def test_fill_record_nullable_order_id(self) -> None:
        """FillRecord order_id can be None (signal-less fills)."""
        from libs.storage.models import FillRecord

        now = datetime.now(timezone.utc)
        record = FillRecord(
            fill_id="fill-002",
            order_id=None,
            portfolio_target="autonomous",
            instrument="BTC-PERP-INTX",
            side="SELL",
            size=Decimal("0.1"),
            price=Decimal("65000.00"),
            fee_usdc=Decimal("1.00"),
            is_maker=True,
            filled_at=now,
            trade_id="trade-002",
        )
        assert record.order_id is None


class TestOrderSignalRecord:
    """Test OrderSignalRecord ORM model instantiation."""

    def test_order_signal_record_instantiation(self) -> None:
        """OrderSignalRecord can be instantiated with all required fields."""
        from libs.storage.models import OrderSignalRecord

        now = datetime.now(timezone.utc)
        record = OrderSignalRecord(
            order_id="order-001",
            signal_id="signal-001",
            portfolio_target="autonomous",
            instrument="ETH-PERP-INTX",
            conviction=0.85,
            primary_source="momentum",
            all_sources="momentum,mean_reversion",
            stop_loss=Decimal("2100.00"),
            take_profit=Decimal("2400.00"),
            limit_price=Decimal("2230.00"),
            leverage=Decimal("2.0"),
            proposed_at=now,
            reasoning="Strong momentum signal",
        )
        assert record.order_id == "order-001"
        assert record.signal_id == "signal-001"
        assert record.portfolio_target == "autonomous"
        assert record.instrument == "ETH-PERP-INTX"
        assert record.conviction == 0.85
        assert record.primary_source == "momentum"
        assert record.all_sources == "momentum,mean_reversion"
        assert record.stop_loss == Decimal("2100.00")
        assert record.take_profit == Decimal("2400.00")
        assert record.limit_price == Decimal("2230.00")
        assert record.leverage == Decimal("2.0")
        assert record.proposed_at == now
        assert record.reasoning == "Strong momentum signal"

    def test_order_signal_record_nullable_fields(self) -> None:
        """OrderSignalRecord nullable fields can be None."""
        from libs.storage.models import OrderSignalRecord

        now = datetime.now(timezone.utc)
        record = OrderSignalRecord(
            order_id="order-002",
            signal_id="signal-002",
            portfolio_target="autonomous",
            instrument="SOL-PERP-INTX",
            conviction=0.75,
            primary_source="funding_arb",
            all_sources="funding_arb",
            stop_loss=None,
            take_profit=None,
            limit_price=None,
            leverage=Decimal("1.0"),
            proposed_at=now,
            reasoning="",
        )
        assert record.stop_loss is None
        assert record.take_profit is None
        assert record.limit_price is None


class TestSignalRecord:
    """Test SignalRecord ORM model instantiation."""

    def test_signal_record_instantiation(self) -> None:
        """SignalRecord can be instantiated with all required fields."""
        from libs.storage.models import SignalRecord

        now = datetime.now(timezone.utc)
        record = SignalRecord(
            signal_id="signal-001",
            timestamp=now,
            instrument="ETH-PERP-INTX",
            source="momentum",
            direction="LONG",
            conviction=0.85,
            time_horizon_seconds=3600,
            reasoning="Strong uptrend detected",
            entry_price=Decimal("2230.50"),
        )
        assert record.signal_id == "signal-001"
        assert record.timestamp == now
        assert record.instrument == "ETH-PERP-INTX"
        assert record.source == "momentum"
        assert record.direction == "LONG"
        assert record.conviction == 0.85
        assert record.time_horizon_seconds == 3600
        assert record.reasoning == "Strong uptrend detected"
        assert record.entry_price == Decimal("2230.50")

    def test_signal_record_nullable_entry_price(self) -> None:
        """SignalRecord entry_price can be None."""
        from libs.storage.models import SignalRecord

        now = datetime.now(timezone.utc)
        record = SignalRecord(
            signal_id="signal-002",
            timestamp=now,
            instrument="BTC-PERP-INTX",
            source="funding_arb",
            direction="SHORT",
            conviction=0.70,
            time_horizon_seconds=7200,
            reasoning="",
            entry_price=None,
        )
        assert record.entry_price is None


class TestBaseMetadata:
    """Test that all three tables are registered in Base.metadata."""

    def test_all_tables_in_metadata(self) -> None:
        """Base.metadata.tables contains fills, order_signals, and signals."""
        from libs.storage.models import Base

        table_names = list(Base.metadata.tables.keys())
        assert "fills" in table_names
        assert "order_signals" in table_names
        assert "signals" in table_names

    def test_fills_table_has_expected_columns(self) -> None:
        """fills table has all required columns."""
        from libs.storage.models import Base

        fills_table = Base.metadata.tables["fills"]
        column_names = {col.name for col in fills_table.columns}
        expected = {
            "fill_id",
            "order_id",
            "portfolio_target",
            "instrument",
            "side",
            "size",
            "price",
            "fee_usdc",
            "is_maker",
            "filled_at",
            "trade_id",
        }
        assert expected.issubset(column_names)

    def test_order_signals_table_has_primary_source_column(self) -> None:
        """order_signals table has primary_source column."""
        from libs.storage.models import Base

        order_signals_table = Base.metadata.tables["order_signals"]
        column_names = {col.name for col in order_signals_table.columns}
        assert "primary_source" in column_names
        assert "all_sources" in column_names
        assert "conviction" in column_names

    def test_signals_table_has_time_horizon_seconds(self) -> None:
        """signals table has time_horizon_seconds column."""
        from libs.storage.models import Base

        signals_table = Base.metadata.tables["signals"]
        column_names = {col.name for col in signals_table.columns}
        assert "time_horizon_seconds" in column_names
        assert "source" in column_names
        assert "direction" in column_names


class TestRelationalStoreSession:
    """Test RelationalStore.session() context manager."""

    def test_session_method_has_aenter(self) -> None:
        """RelationalStore.session() returns an async context manager."""
        from libs.storage.relational import RelationalStore

        store = RelationalStore("postgresql://phantom:phantom_dev@localhost:5432/phantom_perp")
        # session() must return an object with __aenter__ and __aexit__
        ctx = store.session()
        assert hasattr(ctx, "__aenter__"), "session() must return an async context manager"
        assert hasattr(ctx, "__aexit__"), "session() must return an async context manager"

    def test_init_db_is_callable(self) -> None:
        """init_db function exists and is callable."""
        from libs.storage.relational import init_db

        assert callable(init_db)
