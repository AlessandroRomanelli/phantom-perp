"""Live canary test for per-order leverage field on Coinbase Advanced API.

Places real far-below-market LIMIT orders on ETH-PERP at 1x, 2x, 4x, and 8x
leverage, verifies the API accepts each, then cancels immediately.

Orders are placed at 50% of current market price — well outside any fill zone.

Skipped automatically when COINBASE_ADV_API_KEY_A is not set.

Run manually:
    COINBASE_ADV_API_KEY_A=... COINBASE_ADV_API_SECRET_A=... \\
    pytest agents/execution/tests/test_canary_leverage.py -v -s
"""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.product_discovery import discover_and_update_registry
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.config import load_yaml_config
from libs.common.instruments import get_instrument, load_instruments

PRICE_FRACTION = Decimal("0.50")
LEVERAGE_VALUES = [Decimal("1"), Decimal("2"), Decimal("4"), Decimal("8")]


@pytest.mark.integration
class TestLeverageCanary:
    """Verify that per-order leverage values are accepted by the Coinbase Advanced API."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("leverage", LEVERAGE_VALUES, ids=[f"{int(l)}x" for l in LEVERAGE_VALUES])
    async def test_order_accepted_with_leverage(self, leverage: Decimal) -> None:
        """Place an unfillable ETH-PERP limit order with the given leverage and cancel it."""
        api_key = os.environ.get("COINBASE_ADV_API_KEY_A", "")
        api_secret = os.environ.get("COINBASE_ADV_API_SECRET_A", "")
        if not api_key or not api_secret:
            pytest.skip("COINBASE_ADV_API_KEY_A / COINBASE_ADV_API_SECRET_A not set")

        base_url = os.environ.get("COINBASE_ADV_REST_URL", "https://api.coinbase.com")
        portfolio_uuid = os.environ.get("COINBASE_PORTFOLIO_ID", "")

        auth = CoinbaseAuth(api_key=api_key, api_secret=api_secret)
        client = CoinbaseRESTClient(auth=auth, base_url=base_url, portfolio_uuid=portfolio_uuid)

        yaml_config = load_yaml_config("default")
        load_instruments(yaml_config)
        await discover_and_update_registry(client)

        instrument = get_instrument("ETH-PERP")
        product_id: str = instrument.product_id

        product_data: dict[str, str] = await client._request(
            "GET", f"/api/v3/brokerage/products/{product_id}"
        )
        current_price = Decimal(product_data.get("price", "0"))
        assert current_price > 0

        limit_price = (current_price * PRICE_FRACTION).quantize(Decimal("0.01"))
        base_increment = Decimal("0.0001")
        min_size = (Decimal("10") / limit_price).quantize(base_increment, rounding="ROUND_UP")
        order_size = max(min_size, base_increment)

        placed_order_id = ""
        try:
            response = await client.create_order(
                product_id=product_id,
                side="BUY",
                size=order_size,
                order_type="LIMIT",
                limit_price=limit_price,
                client_order_id=f"canary-lev{int(leverage)}-{uuid4()}",
                leverage=leverage,
            )
            placed_order_id = response.order_id

            assert placed_order_id, f"[{leverage}x] order_id must be non-empty"
            assert response.product_id == "ETH-PERP-INTX"
            print(f"\n  ✓ {leverage}x — order_id={placed_order_id}, price={limit_price}")

        finally:
            if placed_order_id:
                await client.cancel_order(placed_order_id)
                print(f"  ✓ {leverage}x — cancelled {placed_order_id}")
            await client.close()
