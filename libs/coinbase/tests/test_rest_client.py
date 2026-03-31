"""Tests for Advanced Trade REST client endpoint paths and response parsing."""

from decimal import Decimal

import pytest
import respx
from httpx import Response

from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.exceptions import OrderRejectedError


@pytest.mark.asyncio
class TestGetProducts:
    async def test_calls_correct_path(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/products").mock(
                return_value=Response(200, json={"products": [
                    {"product_id": "ETH-PERP-INTX", "product_type": "FUTURE"}
                ]})
            )
            result = await client.get_products()
            assert len(result) == 1
            assert result[0].product_id == "ETH-PERP-INTX"

    async def test_filters_by_product_type(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            route = mock.get("/api/v3/brokerage/products").mock(
                return_value=Response(200, json={"products": []})
            )
            await client.get_products(
                product_type="FUTURE", contract_expiry_type="PERPETUAL",
            )
            assert route.calls[0].request.url.params["product_type"] == "FUTURE"


@pytest.mark.asyncio
class TestGetOrderbook:
    async def test_calls_product_book_path(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/product_book").mock(
                return_value=Response(200, json={"pricebook": {
                    "product_id": "ETH-PERP-INTX",
                    "bids": [{"price": "2250", "size": "1.0"}],
                    "asks": [{"price": "2251", "size": "0.5"}],
                }})
            )
            result = await client.get_orderbook("ETH-PERP-INTX")
            assert len(result.bids) == 1
            assert len(result.asks) == 1


@pytest.mark.asyncio
class TestGetCandles:
    async def test_calls_product_candles_path(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/products/ETH-PERP-INTX/candles").mock(
                return_value=Response(200, json={"candles": [
                    {"start": "1700000000", "low": "2200", "high": "2300",
                     "open": "2250", "close": "2280", "volume": "100"}
                ]})
            )
            result = await client.get_candles("ETH-PERP-INTX")
            assert len(result) == 1
            assert result[0].start == "1700000000"


@pytest.mark.asyncio
class TestGetFundingRate:
    async def test_extracts_from_product_details(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/products/ETH-PERP-INTX").mock(
                return_value=Response(200, json={
                    "product_id": "ETH-PERP-INTX",
                    "price": "2250.00",
                    "future_product_details": {
                        "perpetual_details": {
                            "funding_rate": "0.0001",
                            "open_interest": "34109.86",
                        }
                    }
                })
            )
            result = await client.get_funding_rate("ETH-PERP-INTX")
            assert str(result.funding_rate) == "0.0001"
            assert str(result.mark_price) == "2250.00"
            assert result.open_interest == Decimal("34109.86")

    async def test_open_interest_defaults_to_zero_when_absent(self, client: CoinbaseRESTClient) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/products/ETH-PERP-INTX").mock(
                return_value=Response(200, json={
                    "product_id": "ETH-PERP-INTX",
                    "price": "2250.00",
                    "future_product_details": {
                        "perpetual_details": {
                            "funding_rate": "0.0001",
                        }
                    }
                })
            )
            result = await client.get_funding_rate("ETH-PERP-INTX")
            assert result.open_interest == Decimal("0")


@pytest.mark.asyncio
class TestGetPortfolio:
    async def test_includes_portfolio_uuid_in_path(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/intx/portfolio/test-portfolio-uuid").mock(
                return_value=Response(200, json={
                    "portfolios": [{
                        "portfolio_uuid": "test-portfolio-uuid",
                        "collateral": "10000",
                        "portfolio_initial_margin": "500",
                    }],
                    "summary": {
                        "unrealized_pnl": {"value": "50", "currency": "USDC"},
                        "total_balance": {"value": "10050", "currency": "USDC"},
                    }
                })
            )
            result = await client.get_portfolio()
            assert result.portfolio_uuid == "test-portfolio-uuid"
            assert result.collateral == "10000"
            assert result.portfolio_initial_margin == "500"


@pytest.mark.asyncio
class TestGetPositions:
    async def test_includes_portfolio_uuid_in_path(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/intx/positions/test-portfolio-uuid").mock(
                return_value=Response(200, json={"positions": [
                    {"product_id": "ETH-PERP-INTX", "position_side": "LONG",
                     "net_size": "2.5",
                     "unrealized_pnl": {"value": "50", "currency": "USDC"}}
                ]})
            )
            result = await client.get_positions()
            assert len(result) == 1
            assert result[0].product_id == "ETH-PERP-INTX"


@pytest.mark.asyncio
class TestGetOpenOrders:
    async def test_calls_historical_batch_path(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/orders/historical/batch").mock(
                return_value=Response(200, json={"orders": [
                    {"order_id": "ord-1", "status": "OPEN",
                     "product_id": "ETH-PERP-INTX"}
                ]})
            )
            result = await client.get_open_orders()
            assert len(result) == 1


@pytest.mark.asyncio
class TestCreateOrder:
    async def test_uses_order_configuration(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.post("/api/v3/brokerage/orders").mock(
                return_value=Response(200, json={
                    "success": True,
                    "success_response": {
                        "order_id": "new-ord",
                        "product_id": "ETH-PERP-INTX",
                        "status": "PENDING",
                    }
                })
            )
            result = await client.create_order(
                product_id="ETH-PERP-INTX",
                side="BUY",
                size=Decimal("1.0"),
                order_type="LIMIT",
                limit_price=Decimal("2250.00"),
            )
            assert result.order_id == "new-ord"

    async def test_handles_order_failure(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.post("/api/v3/brokerage/orders").mock(
                return_value=Response(200, json={
                    "success": False,
                    "error_response": {
                        "message": "Insufficient funds",
                        "new_order_failure_reason": "INSUFFICIENT_FUND",
                    }
                })
            )
            with pytest.raises(OrderRejectedError):
                await client.create_order(
                    "ETH-PERP-INTX", "BUY", Decimal("1"),
                )

    async def test_stop_market_maps_to_stop_limit_with_slippage(
        self, client: CoinbaseRESTClient,
    ) -> None:
        """STOP_MARKET orders must be translated to stop_limit_stop_limit_gtc
        with a 1% slippage buffer: SELL side limit = stop * 0.99,
        BUY side limit = stop * 1.01."""
        _success_response = {
            "success": True,
            "success_response": {
                "order_id": "sl-ord",
                "product_id": "ETH-PERP-INTX",
                "status": "PENDING",
            },
        }

        # --- SELL stop (stop-loss on a long position) ---
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            route = mock.post("/api/v3/brokerage/orders").mock(
                return_value=Response(200, json=_success_response)
            )
            await client.create_order(
                product_id="ETH-PERP-INTX",
                side="SELL",
                size=Decimal("1.0"),
                order_type="STOP_MARKET",
                stop_price=Decimal("2000.00"),
            )
            import json as _json
            body = _json.loads(route.calls[0].request.content)
            cfg = body["order_configuration"]["stop_limit_stop_limit_gtc"]
            assert cfg["stop_price"] == "2000.00"
            # SELL: limit = stop * 0.99 = 1980.0000
            assert Decimal(cfg["limit_price"]) == Decimal("2000.00") * Decimal("0.99")

        # --- BUY stop (stop-loss on a short position) ---
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            route = mock.post("/api/v3/brokerage/orders").mock(
                return_value=Response(200, json=_success_response)
            )
            await client.create_order(
                product_id="ETH-PERP-INTX",
                side="BUY",
                size=Decimal("1.0"),
                order_type="STOP_MARKET",
                stop_price=Decimal("2500.00"),
            )
            body = _json.loads(route.calls[0].request.content)
            cfg = body["order_configuration"]["stop_limit_stop_limit_gtc"]
            assert cfg["stop_price"] == "2500.00"
            # BUY: limit = stop * 1.01 = 2525.0000
            assert Decimal(cfg["limit_price"]) == Decimal("2500.00") * Decimal("1.01")


@pytest.mark.asyncio
class TestCancelOrder:
    async def test_uses_post_batch_cancel(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            route = mock.post("/api/v3/brokerage/orders/batch_cancel").mock(
                return_value=Response(200, json={"results": [{"success": True}]})
            )
            await client.cancel_order("ord-to-cancel")
            assert route.called


@pytest.mark.asyncio
class TestGetFills:
    async def test_calls_historical_fills_path(
        self, client: CoinbaseRESTClient,
    ) -> None:
        with respx.mock(base_url="https://api.coinbase.com") as mock:
            mock.get("/api/v3/brokerage/orders/historical/fills").mock(
                return_value=Response(200, json={"fills": [
                    {"entry_id": "f1", "order_id": "o1",
                     "product_id": "ETH-PERP-INTX",
                     "side": "BUY", "commission": "0.28"}
                ]})
            )
            result = await client.get_fills(product_id="ETH-PERP-INTX")
            assert len(result) == 1
            assert result[0].commission == "0.28"
