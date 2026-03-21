"""Hourly funding cost projection for ETH-PERP positions.

Funding settles every hour (24 times per day). Each settlement is small
individually but accumulates quickly.  The risk agent must project
cumulative cost over the expected holding period before approving a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from libs.common.constants import FUNDING_SETTLEMENTS_PER_DAY
from libs.common.models.enums import PositionSide


@dataclass(frozen=True)
class FundingCostEstimate:
    """Projected funding cost over a holding period."""

    hourly_cost_usdc: Decimal
    total_cost_usdc: Decimal
    holding_hours: Decimal
    daily_cost_usdc: Decimal
    is_paying: bool  # True if this position pays funding (negative P&L)


def estimate_funding_cost(
    size: Decimal,
    entry_price: Decimal,
    funding_rate: Decimal,
    direction: PositionSide,
    holding_period: timedelta,
) -> FundingCostEstimate:
    """Project funding cost over the expected holding period.

    Positive funding rate means longs pay shorts.
    - LONG + positive rate → paying (negative)
    - LONG + negative rate → receiving (positive)
    - SHORT + positive rate → receiving (positive)
    - SHORT + negative rate → paying (negative)

    Args:
        size: Position size in ETH.
        entry_price: Entry price in USDC.
        funding_rate: Current hourly funding rate (signed).
        direction: LONG or SHORT.
        holding_period: Expected time the position will be held.

    Returns:
        FundingCostEstimate with projected costs.
    """
    notional = size * entry_price

    # Hourly funding payment (from the position holder's perspective)
    # Positive funding: longs pay → hourly_payment negative for longs
    if direction == PositionSide.LONG:
        hourly_payment = -(notional * funding_rate)
    else:
        hourly_payment = notional * funding_rate

    holding_hours = Decimal(str(holding_period.total_seconds())) / Decimal("3600")
    total = hourly_payment * holding_hours
    daily = hourly_payment * FUNDING_SETTLEMENTS_PER_DAY

    return FundingCostEstimate(
        hourly_cost_usdc=hourly_payment.quantize(Decimal("0.01")),
        total_cost_usdc=total.quantize(Decimal("0.01")),
        holding_hours=holding_hours.quantize(Decimal("0.01")),
        daily_cost_usdc=daily.quantize(Decimal("0.01")),
        is_paying=hourly_payment < 0,
    )
