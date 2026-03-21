"""Portfolio state management — build snapshots from Coinbase API data.

The state manager is the source of truth: it queries BOTH Coinbase
portfolios independently and builds PortfolioSnapshot objects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from libs.coinbase.models import PortfolioResponse, PositionResponse
from libs.common.models.enums import PortfolioTarget, PositionSide
from libs.common.models.portfolio import PortfolioSnapshot, SystemSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import utc_now


def build_position(
    resp: PositionResponse,
    portfolio_target: PortfolioTarget,
    *,
    realized_pnl_usdc: Decimal = Decimal("0"),
    cumulative_funding_usdc: Decimal = Decimal("0"),
    total_fees_usdc: Decimal = Decimal("0"),
) -> PerpPosition:
    """Convert a Coinbase PositionResponse to our PerpPosition model.

    Args:
        resp: Raw position data from Coinbase INTX.
        portfolio_target: Which portfolio this position belongs to.
        realized_pnl_usdc: Realized P&L from our internal tracking.
        cumulative_funding_usdc: Cumulative funding from our tracker.
        total_fees_usdc: Total fees from our fill records.
    """
    side = _map_side(resp.side, resp.net_size)
    size = abs(resp.net_size)

    # Compute effective leverage: notional / initial_margin
    notional = size * resp.mark_price
    leverage = (
        notional / resp.initial_margin if resp.initial_margin > 0 else Decimal("0")
    )

    # Margin ratio: maintenance_margin / equity approximation
    margin_ratio = (
        float(resp.maintenance_margin / resp.initial_margin)
        if resp.initial_margin > 0
        else 0.0
    )

    return PerpPosition(
        instrument=resp.instrument_id,
        portfolio_target=portfolio_target,
        side=side,
        size=size,
        entry_price=resp.average_entry_price,
        mark_price=resp.mark_price,
        unrealized_pnl_usdc=resp.unrealized_pnl,
        realized_pnl_usdc=realized_pnl_usdc,
        leverage=leverage,
        initial_margin_usdc=resp.initial_margin,
        maintenance_margin_usdc=resp.maintenance_margin,
        liquidation_price=resp.liquidation_price or Decimal("0"),
        margin_ratio=margin_ratio,
        cumulative_funding_usdc=cumulative_funding_usdc,
        total_fees_usdc=total_fees_usdc,
    )


def build_portfolio_snapshot(
    portfolio_resp: PortfolioResponse,
    position_resps: list[PositionResponse],
    portfolio_target: PortfolioTarget,
    *,
    realized_pnl_today_usdc: Decimal = Decimal("0"),
    funding_pnl_today_usdc: Decimal = Decimal("0"),
    fees_paid_today_usdc: Decimal = Decimal("0"),
    now: datetime | None = None,
) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from Coinbase API responses.

    Args:
        portfolio_resp: Portfolio-level summary from Coinbase.
        position_resps: All positions in this portfolio from Coinbase.
        portfolio_target: A or B.
        realized_pnl_today_usdc: Today's realized P&L from our internal tracking.
        funding_pnl_today_usdc: Today's net funding from our tracker.
        fees_paid_today_usdc: Today's total fees from our fill records.
        now: Timestamp for the snapshot.
    """
    now = now or utc_now()
    positions = [
        build_position(pr, portfolio_target) for pr in position_resps
    ]

    margin_util = (
        float(portfolio_resp.used_margin / portfolio_resp.total_equity * 100)
        if portfolio_resp.total_equity > 0
        else 0.0
    )

    return PortfolioSnapshot(
        timestamp=now,
        portfolio_target=portfolio_target,
        equity_usdc=portfolio_resp.total_equity,
        used_margin_usdc=portfolio_resp.used_margin,
        available_margin_usdc=portfolio_resp.available_margin,
        margin_utilization_pct=margin_util,
        positions=positions,
        unrealized_pnl_usdc=portfolio_resp.unrealized_pnl,
        realized_pnl_today_usdc=realized_pnl_today_usdc,
        funding_pnl_today_usdc=funding_pnl_today_usdc,
        fees_paid_today_usdc=fees_paid_today_usdc,
    )


def build_system_snapshot(
    portfolio_a: PortfolioSnapshot,
    portfolio_b: PortfolioSnapshot,
    now: datetime | None = None,
) -> SystemSnapshot:
    """Combine both portfolio snapshots into a system-wide view."""
    return SystemSnapshot(
        timestamp=now or utc_now(),
        portfolio_a=portfolio_a,
        portfolio_b=portfolio_b,
    )


def _map_side(exchange_side: str, net_size: Decimal) -> PositionSide:
    """Map Coinbase side string + net size to our PositionSide enum."""
    if net_size == 0:
        return PositionSide.FLAT
    side_upper = exchange_side.upper()
    if side_upper in ("LONG", "BUY"):
        return PositionSide.LONG
    if side_upper in ("SHORT", "SELL"):
        return PositionSide.SHORT
    # Fallback: infer from sign
    return PositionSide.LONG if net_size > 0 else PositionSide.SHORT
