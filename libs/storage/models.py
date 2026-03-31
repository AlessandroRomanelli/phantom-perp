"""SQLAlchemy 2.0 ORM table definitions for the tuner's data pipeline.

Three tables map onto the three agent write points:
- fills: written by execution agent at fill event
- order_signals: written by risk agent at approval time
- signals: written by signals agent at emit time
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, NUMERIC, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


class FillRecord(Base):
    """ORM record for a fill event from the exchange.

    Written by the execution agent when a fill is received.
    Nullable order_id accommodates signal-less fills (manual or liquidation).
    """

    __tablename__ = "fills"
    __table_args__ = (
        Index("ix_fills_portfolio_filled_at", "portfolio_target", "filled_at"),
        Index("ix_fills_instrument_filled_at", "instrument", "filled_at"),
    )

    fill_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Nullable: signal-less fills (manual trades, liquidations) have no order_id
    order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Stores Route.value e.g. "autonomous" | "user_confirmed"
    portfolio_target: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stores OrderSide.value e.g. "BUY" | "SELL"
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    size: Mapped[Decimal] = mapped_column(NUMERIC(20, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(NUMERIC(20, 8), nullable=False)
    fee_usdc: Mapped[Decimal] = mapped_column(NUMERIC(20, 8), nullable=False)
    is_maker: Mapped[bool] = mapped_column(Boolean, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_id: Mapped[str] = mapped_column(String, nullable=False)


class OrderSignalRecord(Base):
    """ORM record for an order-to-signal attribution entry.

    Written by the risk agent at approval time. Links order_id to the
    originating signal's primary source, enabling per-strategy P&L attribution.
    The INNER JOIN path is: fills.order_id -> order_signals.order_id -> primary_source.
    """

    __tablename__ = "order_signals"
    __table_args__ = (
        Index(
            "ix_order_signals_primary_source_proposed_at", "primary_source", "proposed_at"
        ),
        Index("ix_order_signals_portfolio_proposed_at", "portfolio_target", "proposed_at"),
    )

    order_id: Mapped[str] = mapped_column(String, primary_key=True)
    signal_id: Mapped[str] = mapped_column(String, nullable=False)
    # Stores Route.value e.g. "autonomous"
    portfolio_target: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str] = mapped_column(String(32), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    # Stores SignalSource.value of the highest-conviction contributing source
    primary_source: Mapped[str] = mapped_column(String(64), nullable=False)
    # Comma-separated SignalSource.value strings for all contributing sources
    all_sources: Mapped[str] = mapped_column(Text, nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(NUMERIC(20, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(NUMERIC(20, 8), nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(NUMERIC(20, 8), nullable=True)
    leverage: Mapped[Decimal] = mapped_column(NUMERIC(10, 4), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SignalRecord(Base):
    """ORM record for a signal emitted by a strategy.

    Written by the signals agent at emit time. Stores signal metadata
    for conviction-outcome correlation and signal lifecycle analysis.
    """

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_source_timestamp", "source", "timestamp"),
        Index("ix_signals_instrument_timestamp", "instrument", "timestamp"),
    )

    signal_id: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stores SignalSource.value e.g. "momentum"
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stores PositionSide.value e.g. "LONG" | "SHORT"
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    # timedelta.total_seconds() as integer
    time_horizon_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entry_price: Mapped[Decimal | None] = mapped_column(NUMERIC(20, 8), nullable=True)
