"""Live canary integration test for ETH-PERP order placement and cancellation.

Places a real min-notional BUY LIMIT order well below market on Coinbase
Advanced Trade, verifies the response fields, then cancels the order.
Guarantees the cancel via try/finally so no orphaned orders are left.

Skipped automatically when COINBASE_ADV_API_KEY_A is not set — safe to run
in CI without credentials.

Run manually with credentials:
    COINBASE_ADV_API_KEY_A=... COINBASE_ADV_API_SECRET_A=... \\
    pytest agents/execution/tests/test_canary_live.py -v
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


@pytest.mark.integration
class TestLiveCanaryOrder:
    """Live order placement and cancellation canary test.

    Requires COINBASE_ADV_API_KEY_A and COINBASE_ADV_API_SECRET_A environment
    variables to be set.  The test is skipped silently when they are absent.
    """

    @pytest.mark.asyncio
    async def test_place_and_cancel_eth_perp_limit_order(self) -> None:
        """Place a far-below-market ETH-PERP limit order and immediately cancel it.

        The order is placed at 75% of current market price (well below any
        realistic fill zone) with the minimum size that satisfies Coinbase's
        $10 quote_min_size notional requirement.  We verify the API response
        fields (order_id, product_id) and then unconditionally cancel.
        """
        api_key = os.environ.get("COINBASE_ADV_API_KEY_A", "")
        api_secret = os.environ.get("COINBASE_ADV_API_SECRET_A", "")

        if not api_key or not api_secret:
            pytest.skip("COINBASE_ADV_API_KEY_A / COINBASE_ADV_API_SECRET_A not set")

        base_url: str = os.environ.get(
            "COINBASE_ADV_REST_URL", "https://api.coinbase.com"
        )
        portfolio_uuid: str = os.environ.get("COINBASE_ROUTE_A_ID", "")

        # -- Build auth + client ------------------------------------------------
        auth = CoinbaseAuth(api_key=api_key, api_secret=api_secret)
        client = CoinbaseRESTClient(
            auth=auth,
            base_url=base_url,
            portfolio_uuid=portfolio_uuid,
        )

        # -- Populate instrument registry from YAML then discover product IDs ---
        yaml_config = load_yaml_config("default")
        load_instruments(yaml_config)
        await discover_and_update_registry(client)

        instrument = get_instrument("ETH-PERP")
        product_id: str = instrument.product_id  # e.g. "ETH-PERP-INTX"

        # -- Compute a safe limit price and minimum valid size ------------------
        # Coinbase rejects limit prices outside a price band and orders below
        # $10 notional (quote_min_size). Use 75% of current mark price and
        # compute the minimum size to satisfy the $10 floor.
        product_data: dict[str, str] = await client._request(
            "GET", f"/api/v3/brokerage/products/{product_id}"
        )
        current_price = Decimal(product_data.get("price", "0"))
        assert current_price > 0, "Could not fetch current ETH price"

        limit_price = (current_price * Decimal("0.75")).quantize(Decimal("0.01"))
        quote_min = Decimal("10")  # Coinbase quote_min_size for ETH-PERP-INTX
        base_increment = Decimal(product_data.get("base_increment", "0.0001"))
        min_size = (quote_min / limit_price).quantize(
            base_increment, rounding="ROUND_UP"
        )
        order_size = max(min_size, base_increment)
        client_order_id: str = f"canary-{uuid4()}"

        # -- Place + verify + cancel (finally guarantees cancel) ----------------
        placed_order_id: str = ""
        try:
            response = await client.create_order(
                product_id=product_id,
                side="BUY",
                size=order_size,
                order_type="LIMIT",
                limit_price=limit_price,
                client_order_id=client_order_id,
            )

            # Capture immediately so the finally block can cancel even if
            # an assertion below fails.
            placed_order_id = response.order_id

            # Core correctness assertions
            assert placed_order_id, "order_id must be non-empty"
            assert response.product_id == "ETH-PERP-INTX", (
                f"Expected product_id='ETH-PERP-INTX', got '{response.product_id}'"
            )

        finally:
            # Always cancel — even if assertions above failed.
            # Guard against the case where create_order itself raised before
            # we got a response (placed_order_id stays empty).
            if placed_order_id:
                await client.cancel_order(placed_order_id)
            await client.close()
