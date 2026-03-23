"""Tests for Advanced Trade response models."""

from libs.coinbase.models import (
    Amount,
    CandleResponse,
    FillResponse,
    OrderBookLevel,
    OrderBookResponse,
    OrderResponse,
    PortfolioResponse,
    PositionResponse,
    ProductResponse,
)


class TestAmount:
    def test_parse_amount(self) -> None:
        a = Amount.model_validate({"value": "123.45", "currency": "USDC"})
        assert a.value == "123.45"
        assert a.currency == "USDC"


class TestProductResponse:
    def test_parse_product(self) -> None:
        data = {
            "product_id": "ETH-PERP-INTX",
            "product_type": "FUTURE",
            "base_display_symbol": "ETH",
            "base_currency_id": "ETH",
            "quote_currency_id": "USDC",
            "product_venue": "INTX",
            "status": "online",
        }
        p = ProductResponse.model_validate(data)
        assert p.product_id == "ETH-PERP-INTX"
        assert p.base_display_symbol == "ETH"
        assert p.product_venue == "INTX"


class TestCandleResponse:
    def test_parse_candle_string_fields(self) -> None:
        data = {
            "start": "1700000000",
            "low": "2200.50",
            "high": "2300.75",
            "open": "2250.00",
            "close": "2280.25",
            "volume": "1500.5",
        }
        c = CandleResponse.model_validate(data)
        assert c.start == "1700000000"
        assert c.volume == "1500.5"


class TestPositionResponse:
    def test_parse_position_with_amount(self) -> None:
        data = {
            "product_id": "ETH-PERP-INTX",
            "portfolio_uuid": "abc-123",
            "position_side": "LONG",
            "net_size": "2.5",
            "unrealized_pnl": {"value": "50.00", "currency": "USDC"},
            "mark_price": {"value": "2250.00", "currency": "USD"},
        }
        p = PositionResponse.model_validate(data)
        assert p.product_id == "ETH-PERP-INTX"
        assert p.unrealized_pnl is not None
        assert p.unrealized_pnl.value == "50.00"
        assert p.mark_price is not None
        assert p.mark_price.value == "2250.00"


class TestPortfolioResponse:
    def test_parse_portfolio(self) -> None:
        data = {
            "portfolio_uuid": "abc-123",
            "collateral": "10000.00",
            "unrealized_pnl": {"value": "250.00", "currency": "USDC"},
            "total_balance": {"value": "10250.00", "currency": "USDC"},
            "portfolio_initial_margin": "2000.00",
            "in_liquidation": False,
        }
        p = PortfolioResponse.model_validate(data)
        assert p.portfolio_uuid == "abc-123"
        assert p.unrealized_pnl is not None
        assert p.unrealized_pnl.value == "250.00"


class TestFillResponse:
    def test_parse_fill_renamed_fields(self) -> None:
        data = {
            "entry_id": "fill-001",
            "order_id": "order-001",
            "product_id": "ETH-PERP-INTX",
            "side": "BUY",
            "size": "1.0",
            "price": "2250.00",
            "commission": "0.28",
            "trade_time": "2026-03-23T10:00:00Z",
        }
        f = FillResponse.model_validate(data)
        assert f.entry_id == "fill-001"
        assert f.commission == "0.28"
        assert f.product_id == "ETH-PERP-INTX"


class TestOrderResponse:
    def test_parse_order(self) -> None:
        data = {
            "order_id": "ord-001",
            "product_id": "ETH-PERP-INTX",
            "side": "BUY",
            "order_type": "LIMIT",
            "status": "OPEN",
            "base_size": "1.0",
            "limit_price": "2250.00",
        }
        o = OrderResponse.model_validate(data)
        assert o.order_id == "ord-001"
        assert o.base_size == "1.0"


class TestOrderBookResponse:
    def test_parse_orderbook(self) -> None:
        data = {
            "product_id": "ETH-PERP-INTX",
            "bids": [{"price": "2250", "size": "1.0"}],
            "asks": [{"price": "2251", "size": "0.5"}],
        }
        ob = OrderBookResponse.model_validate(data)
        assert len(ob.bids) == 1
        assert len(ob.asks) == 1
        assert ob.bids[0].price == 2250
