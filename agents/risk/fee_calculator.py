"""Fee estimation for ETH-PERP at VIP 1 tier."""

from __future__ import annotations

from decimal import Decimal

from libs.common.constants import FEE_MAKER, FEE_TAKER


def estimate_fee(
    size: Decimal,
    price: Decimal,
    is_maker: bool = True,
) -> Decimal:
    """Estimate the trading fee for an order.

    The system defaults to limit (maker) orders to minimize fees.
    Maker: 0.0125%, Taker: 0.0250%.

    Args:
        size: Order size in ETH.
        price: Order price in USDC.
        is_maker: True for limit orders, False for market/IOC.

    Returns:
        Estimated fee in USDC.
    """
    notional = size * price
    rate = FEE_MAKER if is_maker else FEE_TAKER
    return (notional * rate).quantize(Decimal("0.01"))
