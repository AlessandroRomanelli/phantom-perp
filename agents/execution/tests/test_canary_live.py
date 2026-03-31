"""Live canary integration test for ETH-PERP order placement and cancellation.

Places a real min-size BUY LIMIT order far below market ($1.00) on Coinbase
Advanced Trade, verifies the response fields, then cancels the order.
Guarantees the cancel via try/finally so no orphaned orders are left.

Skipped automatically when COINBASE_ADV_API_KEY_A is not set — safe to run
in CI without credentials.

Run manually with credentials:
    COINBASE_ADV_API_KEY_A=... COINBASE_ADV_API_SECRET_A=... \\
    COINBASE_PORTFOLIO_A_ID=... \\
    pytest agents/execution/tests/test_canary_live.py -v -m integration
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

        The order is placed at $1.00 (far below any realistic ETH price) with
        the minimum order size so fill risk is zero.  We verify the API response
        fields (order_id, product_id, status) and then unconditionally cancel.
        """
        api_key = os.environ.get("COINBASE_ADV_API_KEY_A", "")
        api_secret = os.environ.get("COINBASE_ADV_API_SECRET_A", "")

        if not api_key or not api_secret:
            pytest.skip("COINBASE_ADV_API_KEY_A / COINBASE_ADV_API_SECRET_A not set")

        base_url: str = os.environ.get(
            "COINBASE_ADV_REST_URL", "https://api.coinbase.com"
        )
        portfolio_uuid: str = os.environ.get("COINBASE_PORTFOLIO_A_ID", "")

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
        order_size: Decimal = instrument.min_order_size  # 0.0001
        limit_price: Decimal = Decimal("1.00")  # far below market — zero fill risk
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
            assert response.status in ("OPEN", "PENDING"), (
                f"Expected status OPEN or PENDING, got '{response.status}'"
            )

        finally:
            # Always cancel — even if assertions above failed.
            # Guard against the case where create_order itself raised before
            # we got a response (placed_order_id stays empty).
            if placed_order_id:
                await client.cancel_order(placed_order_id)
            await client.close()
