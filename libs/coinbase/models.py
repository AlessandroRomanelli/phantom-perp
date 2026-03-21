"""Pydantic models for Coinbase INTX API responses."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InstrumentResponse(BaseModel):
    """Coinbase INTX instrument metadata."""

    instrument_id: str
    instrument_uuid: str
    symbol: str
    type: str
    base_asset_id: str
    quote_asset_id: str
    base_increment: Decimal
    quote_increment: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    base_asset_name: str
    quote_asset_name: str
    trading: bool


class OrderBookLevel(BaseModel):
    """Single level in the order book."""

    price: Decimal
    size: Decimal
    num_orders: int = 0


class OrderBookResponse(BaseModel):
    """L2 order book snapshot."""

    instrument_id: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime | None = None


class CandleResponse(BaseModel):
    """OHLCV candle."""

    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class FundingRateResponse(BaseModel):
    """Funding rate from Coinbase INTX."""

    instrument_id: str
    funding_rate: Decimal
    mark_price: Decimal
    event_time: datetime


class OrderResponse(BaseModel):
    """Order creation / status response."""

    order_id: str
    client_order_id: str = ""
    instrument_id: str
    portfolio_id: str
    side: str
    type: str
    size: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    status: str
    filled_size: Decimal = Decimal("0")
    filled_value: Decimal = Decimal("0")
    average_filled_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    created_at: datetime | None = None


class PositionResponse(BaseModel):
    """Position from Coinbase INTX."""

    instrument_id: str
    portfolio_id: str
    side: str
    net_size: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    liquidation_price: Decimal | None = None
    initial_margin: Decimal = Decimal("0")
    maintenance_margin: Decimal = Decimal("0")


class PortfolioResponse(BaseModel):
    """Portfolio / account summary from Coinbase INTX."""

    portfolio_id: str
    name: str = ""
    total_equity: Decimal = Decimal("0")
    available_margin: Decimal = Decimal("0")
    used_margin: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    margin_utilization: Decimal = Decimal("0")


class FillResponse(BaseModel):
    """Fill event from Coinbase INTX."""

    fill_id: str
    order_id: str
    instrument_id: str
    portfolio_id: str
    side: str
    size: Decimal
    price: Decimal
    fee: Decimal
    liquidity: str = ""  # "MAKER" or "TAKER"
    trade_id: str = ""
    filled_at: datetime = Field(default_factory=datetime.now)
