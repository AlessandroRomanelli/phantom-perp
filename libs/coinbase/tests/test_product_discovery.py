"""Tests for dynamic product ID discovery (MIG-04, D-14 through D-17)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from libs.coinbase.models import ProductResponse
from libs.coinbase.product_discovery import discover_and_update_registry, discover_product_ids


def _make_product(
    product_id: str,
    base_symbol: str,
    venue: str = "INTX",
    product_type: str = "FUTURE",
) -> ProductResponse:
    """Helper to create ProductResponse for testing."""
    return ProductResponse(
        product_id=product_id,
        product_type=product_type,
        base_display_symbol=base_symbol,
        base_currency_id=base_symbol,
        product_venue=venue,
    )


@pytest.mark.asyncio
class TestDiscoverProductIds:
    async def test_resolves_matching_symbols(self) -> None:
        """Discovery returns mapping for symbols found in API response."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = [
            _make_product("ETH-PERP-INTX", "ETH"),
            _make_product("BTC-PERP-INTX", "BTC"),
            _make_product("SOL-PERP-INTX", "SOL"),
        ]

        result = await discover_product_ids(mock_client, ["ETH", "BTC", "SOL"])

        assert result == {
            "ETH": "ETH-PERP-INTX",
            "BTC": "BTC-PERP-INTX",
            "SOL": "SOL-PERP-INTX",
        }

    async def test_filters_by_intx_venue(self) -> None:
        """Only INTX venue products are matched (D-15)."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = [
            _make_product("ETH-PERP-INTX", "ETH", venue="INTX"),
            _make_product("ETH-PERP-OTHER", "ETH", venue="CBE"),
        ]

        result = await discover_product_ids(mock_client, ["ETH"])

        assert result == {"ETH": "ETH-PERP-INTX"}

    async def test_missing_symbols_omitted_not_error(self) -> None:
        """Symbols not found in API are absent from result (no crash)."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = [
            _make_product("ETH-PERP-INTX", "ETH"),
        ]

        result = await discover_product_ids(mock_client, ["ETH", "QQQ", "SPY"])

        assert result == {"ETH": "ETH-PERP-INTX"}
        assert "QQQ" not in result
        assert "SPY" not in result

    async def test_calls_with_future_perpetual_filters(self) -> None:
        """get_products is called with product_type=FUTURE, contract_expiry_type=PERPETUAL."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = []

        await discover_product_ids(mock_client, ["ETH"])

        mock_client.get_products.assert_called_once_with(
            product_type="FUTURE",
            contract_expiry_type="PERPETUAL",
        )

    async def test_matches_base_display_symbol(self) -> None:
        """Matching uses base_display_symbol field."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = [
            _make_product("QQQ-PERP-INTX", "QQQ"),
            _make_product("SPY-PERP-INTX", "SPY"),
        ]

        result = await discover_product_ids(mock_client, ["QQQ", "SPY"])

        assert result == {"QQQ": "QQQ-PERP-INTX", "SPY": "SPY-PERP-INTX"}

    async def test_empty_products_returns_empty_mapping(self) -> None:
        """If API returns no products, result is empty (not an error)."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = []

        result = await discover_product_ids(mock_client, ["ETH", "BTC"])

        assert result == {}


@pytest.mark.asyncio
class TestDiscoverAndUpdateRegistry:
    async def test_updates_registry_with_resolved_ids(self) -> None:
        """discover_and_update_registry calls update_registry_product_ids with mapping."""
        mock_client = AsyncMock()
        mock_client.get_products.return_value = [
            _make_product("ETH-PERP-INTX", "ETH"),
        ]

        from libs.common.instruments import _registry, load_instruments

        # Set up registry with one instrument
        load_instruments(
            {
                "instruments": [
                    {
                        "id": "ETH-PERP",
                        "base_currency": "ETH",
                        "quote_currency": "USDC",
                        "tick_size": 0.01,
                        "min_order_size": 0.0001,
                    }
                ]
            }
        )

        assert _registry["ETH-PERP"].resolved_product_id == ""

        result = await discover_and_update_registry(mock_client)

        assert result == {"ETH": "ETH-PERP-INTX"}
        assert _registry["ETH-PERP"].resolved_product_id == "ETH-PERP-INTX"
        assert _registry["ETH-PERP"].product_id == "ETH-PERP-INTX"
        assert _registry["ETH-PERP"].ws_product_id == "ETH-PERP-INTX"
