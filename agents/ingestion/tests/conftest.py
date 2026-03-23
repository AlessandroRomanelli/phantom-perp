"""Shared test fixtures for ingestion agent tests."""

from libs.common.instruments import load_instruments

# Load instrument registry so that get_instrument() / get_all_instruments()
# calls work inside ingestion tests. Runs once at test collection time.
load_instruments({
    "instruments": [
        {
            "id": "ETH-PERP",
            "base_currency": "ETH",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.0001,
        },
        {
            "id": "BTC-PERP",
            "base_currency": "BTC",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.00001,
        },
        {
            "id": "SOL-PERP",
            "base_currency": "SOL",
            "quote_currency": "USDC",
            "tick_size": 0.001,
            "min_order_size": 0.01,
        },
        {
            "id": "QQQ-PERP",
            "base_currency": "QQQ",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.001,
        },
        {
            "id": "SPY-PERP",
            "base_currency": "SPY",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.001,
        },
    ]
})
