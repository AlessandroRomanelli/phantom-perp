"""Dynamic product ID discovery for Coinbase Advanced Trade API.

Resolves canonical symbols (ETH, BTC, SOL, QQQ, SPY) to actual Advanced Trade
product IDs (e.g., ETH-PERP-INTX) at startup. Per D-14: product IDs are
discovered dynamically, not hardcoded.
"""

from __future__ import annotations

import structlog

from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.instruments import get_all_instruments, update_registry_product_ids

logger = structlog.get_logger(__name__)


async def discover_product_ids(
    client: CoinbaseRESTClient,
    symbols: list[str],
) -> dict[str, str]:
    """Resolve canonical symbols to Advanced Trade product IDs.

    Calls GET /api/v3/brokerage/products with FUTURE+PERPETUAL filters (D-15),
    then matches base_display_symbol against the requested symbols list,
    filtering to INTX venue products only.

    Args:
        client: Authenticated REST client.
        symbols: Base currency symbols to resolve (e.g., ["ETH", "BTC", "SOL"]).

    Returns:
        Mapping of symbol to product_id (e.g., {"ETH": "ETH-PERP-INTX"}).
        Symbols not found in the API response are omitted (not an error).
    """
    products = await client.get_products(
        product_type="FUTURE",
        contract_expiry_type="PERPETUAL",
    )

    mapping: dict[str, str] = {}
    symbol_set = set(symbols)

    for product in products:
        if product.product_venue != "INTX":
            continue
        # base_display_symbol may be empty in Advanced Trade API responses;
        # fall back to extracting the symbol from product_id (e.g., "ETH-PERP-INTX" -> "ETH")
        base_symbol = (
            product.base_display_symbol
            or product.base_currency_id
            or product.product_id.split("-")[0]
        )
        if base_symbol in symbol_set:
            mapping[base_symbol] = product.product_id

    # Log warnings for any symbols not found
    missing = symbol_set - set(mapping.keys())
    if missing:
        logger.warning(
            "product_ids_not_found",
            missing_symbols=sorted(missing),
            found=mapping,
        )

    logger.info(
        "product_ids_discovered",
        count=len(mapping),
        mapping=mapping,
    )

    return mapping


async def discover_and_update_registry(client: CoinbaseRESTClient) -> dict[str, str]:
    """Discover product IDs and update the instrument registry.

    Reads configured symbols from the instrument registry, discovers their
    product IDs via the API, and updates the registry with resolved IDs.
    This is the main startup function called by agents (D-16, D-17).

    Args:
        client: Authenticated REST client.

    Returns:
        The symbol-to-product_id mapping that was applied.
    """
    instruments = get_all_instruments()
    symbols = [inst.base_currency for inst in instruments]

    mapping = await discover_product_ids(client, symbols)
    update_registry_product_ids(mapping)

    return mapping
