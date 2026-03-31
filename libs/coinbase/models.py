"""Pydantic models for Coinbase Advanced Trade API responses."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class Amount(BaseModel):
    """Monetary value with currency from Advanced Trade API."""

    value: str
    currency: str


class ProductResponse(BaseModel):
    """Product metadata from Advanced Trade API."""

    product_id: str
    product_type: str = ""
    base_display_symbol: str = ""
    base_currency_id: str = ""
    quote_currency_id: str = ""
    base_increment: str = "0"
    quote_increment: str = "0"
    base_min_size: str = "0"
    base_max_size: str = "0"
    trading_disabled: bool = False
    status: str = ""
    product_venue: str = ""
    # Perpetual-specific nested fields
    future_product_details: dict[str, Any] | None = None


class OrderBookLevel(BaseModel):
    """Single level in the order book."""

    price: Decimal
    size: Decimal


class OrderBookResponse(BaseModel):
    """L2 order book snapshot."""

    product_id: str = ""
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    time: str = ""


class CandleResponse(BaseModel):
    """OHLCV candle from Advanced Trade API.

    All numeric fields are strings. Start is a UNIX timestamp string.
    """

    start: str
    low: str
    high: str
    open: str
    close: str
    volume: str


class FundingRateResponse(BaseModel):
    """Funding rate extracted from product details.

    Advanced Trade has no dedicated funding endpoint. The funding rate
    and open interest are extracted from the product details response under
    future_product_details.perpetual_details.
    """

    product_id: str
    funding_rate: Decimal
    mark_price: Decimal = Decimal("0")
    open_interest: Decimal = Decimal("0")


class OrderResponse(BaseModel):
    """Order creation / status response from Advanced Trade API."""

    order_id: str
    client_order_id: str = ""
    product_id: str = ""
    side: str = ""
    order_type: str = ""
    status: str = ""
    base_size: str = "0"
    limit_price: str = "0"
    filled_size: str = "0"
    filled_value: str = "0"
    average_filled_price: str = "0"
    total_fees: str = "0"
    created_time: str = ""


class PositionResponse(BaseModel):
    """Position from Advanced Trade /intx/positions endpoint.

    Most monetary fields use Amount objects {value, currency}.
    """

    product_id: str
    portfolio_uuid: str = ""
    symbol: str = ""
    position_side: str = ""  # "LONG" or "SHORT"
    net_size: str = "0"
    entry_vwap: Amount | None = None
    mark_price: Amount | None = None
    unrealized_pnl: Amount | None = None
    liquidation_price: Amount | None = None
    im_contribution: str = "0"
    aggregated_pnl: Amount | None = None


class PortfolioResponse(BaseModel):
    """Portfolio summary from Advanced Trade /intx/portfolio endpoint."""

    portfolio_uuid: str = ""
    collateral: str = "0"
    unrealized_pnl: Amount | None = None
    total_balance: Amount | None = None
    portfolio_initial_margin: str = "0"
    portfolio_maintenance_margin: str = "0"
    in_liquidation: bool = False


class FillResponse(BaseModel):
    """Fill event from Advanced Trade API."""

    entry_id: str
    order_id: str
    product_id: str
    side: str
    size: str = "0"
    price: str = "0"
    commission: str = "0"
    liquidity_indicator: str = ""
    trade_id: str = ""
    trade_time: str = ""
