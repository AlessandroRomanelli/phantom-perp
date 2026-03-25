"""Realized + unrealized P&L, funding-adjusted, fee-adjusted, per portfolio.

All P&L values are in USDC. Calculations are based on:
  - Realized P&L: from closed fills (exit_price - entry_price) * size
  - Unrealized P&L: from Coinbase mark price (mark_price - entry_price) * size
  - Funding P&L: cumulative hourly funding payments
  - Fee costs: total maker + taker fees paid
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.common.models.enums import OrderSide
from libs.common.models.order import Fill


@dataclass(frozen=True, slots=True)
class PnLSummary:
    """P&L breakdown for a portfolio."""

    realized_pnl_usdc: Decimal
    unrealized_pnl_usdc: Decimal
    funding_pnl_usdc: Decimal
    total_fees_usdc: Decimal
    maker_fees_usdc: Decimal
    taker_fees_usdc: Decimal
    fill_count: int

    @property
    def net_pnl_usdc(self) -> Decimal:
        """Net P&L: realized + unrealized + funding - fees."""
        return (
            self.realized_pnl_usdc
            + self.unrealized_pnl_usdc
            + self.funding_pnl_usdc
            - self.total_fees_usdc
        )

    @property
    def maker_ratio(self) -> float:
        """Fraction of fees that were maker (lower is better for cost)."""
        if self.total_fees_usdc == 0:
            return 0.0
        return float(self.maker_fees_usdc / self.total_fees_usdc)


def compute_fees_from_fills(fills: list[Fill]) -> tuple[Decimal, Decimal, Decimal]:
    """Compute fee breakdown from fills.

    Returns:
        (total_fees, maker_fees, taker_fees) all in USDC.
    """
    total = Decimal("0")
    maker = Decimal("0")
    taker = Decimal("0")
    for fill in fills:
        total += fill.fee_usdc
        if fill.is_maker:
            maker += fill.fee_usdc
        else:
            taker += fill.fee_usdc
    return total, maker, taker


def compute_realized_pnl(fills: list[Fill]) -> Decimal:
    """Compute realized P&L from fills using average-cost matching.

    Tracks signed net_sizes per instrument: positive = long, negative = short.
    BUY fills increase net_size; SELL fills decrease it.  When a fill moves
    net_size *through* zero (or towards it), the portion that reduces the
    existing position realizes P&L against the average entry price.
    """
    # cost_basis tracks the *absolute* cost of the current position
    # (avg_entry * abs(net_size)).  net_sizes is signed.
    cost_basis: dict[str, Decimal] = {}
    net_sizes: dict[str, Decimal] = {}
    realized: Decimal = Decimal("0")

    for fill in sorted(fills, key=lambda f: f.filled_at):
        inst = fill.instrument
        if inst not in cost_basis:
            cost_basis[inst] = Decimal("0")
            net_sizes[inst] = Decimal("0")

        # Signed delta: BUY is +size, SELL is -size
        delta = fill.size if fill.side == OrderSide.BUY else -fill.size
        prev_net = net_sizes[inst]

        if prev_net == 0:
            # Opening a fresh position (long or short)
            net_sizes[inst] = delta
            cost_basis[inst] = fill.size * fill.price
        elif (prev_net > 0 and delta > 0) or (prev_net < 0 and delta < 0):
            # Adding to existing position in same direction
            net_sizes[inst] += delta
            cost_basis[inst] += fill.size * fill.price
        else:
            # Reducing or flipping position
            abs_prev = abs(prev_net)
            abs_delta = fill.size
            avg_entry = cost_basis[inst] / abs_prev

            closed_size = min(abs_delta, abs_prev)
            if prev_net > 0:
                # Was long, SELL is closing — P&L = (exit - entry) * size
                realized += closed_size * (fill.price - avg_entry)
            else:
                # Was short, BUY is closing — P&L = (entry - exit) * size
                realized += closed_size * (avg_entry - fill.price)

            # Reduce cost basis proportionally
            cost_basis[inst] -= closed_size * avg_entry
            net_sizes[inst] += delta

            # If we flipped direction, start new cost basis for the remainder
            remainder = abs_delta - abs_prev
            if remainder > 0:
                cost_basis[inst] = remainder * fill.price

    return realized


def build_pnl_summary(
    fills: list[Fill],
    unrealized_pnl_usdc: Decimal,
    funding_pnl_usdc: Decimal,
) -> PnLSummary:
    """Build a complete P&L summary from fills and external data.

    Args:
        fills: All fills for this portfolio.
        unrealized_pnl_usdc: From Coinbase PositionResponse or PortfolioResponse.
        funding_pnl_usdc: From our FundingTracker.
    """
    total_fees, maker_fees, taker_fees = compute_fees_from_fills(fills)
    realized = compute_realized_pnl(fills)

    return PnLSummary(
        realized_pnl_usdc=realized,
        unrealized_pnl_usdc=unrealized_pnl_usdc,
        funding_pnl_usdc=funding_pnl_usdc,
        total_fees_usdc=total_fees,
        maker_fees_usdc=maker_fees,
        taker_fees_usdc=taker_fees,
        fill_count=len(fills),
    )
