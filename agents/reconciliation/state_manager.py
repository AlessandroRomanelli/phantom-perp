"""Portfolio state management — build snapshots from Coinbase API data.

The state manager is the source of truth: it queries BOTH Coinbase
portfolios independently and builds PortfolioSnapshot objects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from libs.coinbase.models import Amount, PortfolioResponse, PositionResponse
from libs.common.exceptions import PortfolioMismatchError
from libs.common.models.enums import Route, PositionSide
from libs.common.models.portfolio import PortfolioSnapshot, SystemSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import utc_now


def _amount_to_decimal(amt: Amount | None, fallback: Decimal = Decimal("0")) -> Decimal:
    """Extract Decimal value from an Amount object."""
    if amt is None:
        return fallback
    return Decimal(amt.value) if amt.value else fallback


def build_position(
    resp: PositionResponse,
    route: Route,
    *,
    realized_pnl_usdc: Decimal = Decimal("0"),
    cumulative_funding_usdc: Decimal = Decimal("0"),
    total_fees_usdc: Decimal = Decimal("0"),
) -> PerpPosition:
    """Convert a Coinbase PositionResponse to our PerpPosition model.

    Args:
        resp: Raw position data from Coinbase Advanced Trade API.
        route: Which route this position belongs to.
        realized_pnl_usdc: Realized P&L from our internal tracking.
        cumulative_funding_usdc: Cumulative funding from our tracker.
        total_fees_usdc: Total fees from our fill records.
    """
    net_size = Decimal(resp.net_size) if resp.net_size else Decimal("0")
    side = _map_side(resp.position_side, net_size)
    size = abs(net_size)

    mark_price = _amount_to_decimal(resp.mark_price)
    entry_price = _amount_to_decimal(resp.entry_vwap)
    unrealized_pnl = _amount_to_decimal(resp.unrealized_pnl)
    liquidation_price = _amount_to_decimal(resp.liquidation_price)
    initial_margin = Decimal(resp.im_contribution) if resp.im_contribution else Decimal("0")

    # Compute effective leverage: notional / initial_margin
    notional = size * mark_price
    leverage = (
        notional / initial_margin if initial_margin > 0 else Decimal("0")
    )

    # Margin ratio: approximate from im_contribution (no separate maintenance field in Advanced API)
    margin_ratio = 0.5 if initial_margin > 0 else 0.0

    return PerpPosition(
        instrument=resp.product_id,
        route=route,
        side=side,
        size=size,
        entry_price=entry_price,
        mark_price=mark_price,
        unrealized_pnl_usdc=unrealized_pnl,
        realized_pnl_usdc=realized_pnl_usdc,
        leverage=leverage,
        initial_margin_usdc=initial_margin,
        maintenance_margin_usdc=initial_margin / 2 if initial_margin > 0 else Decimal("0"),
        liquidation_price=liquidation_price,
        margin_ratio=margin_ratio,
        cumulative_funding_usdc=cumulative_funding_usdc,
        total_fees_usdc=total_fees_usdc,
    )


def build_portfolio_snapshot(
    portfolio_resp: PortfolioResponse,
    position_resps: list[PositionResponse],
    route: Route,
    *,
    expected_portfolio_id: str | None = None,
    realized_pnl_today_usdc: Decimal = Decimal("0"),
    funding_pnl_today_usdc: Decimal = Decimal("0"),
    fees_paid_today_usdc: Decimal = Decimal("0"),
    now: datetime | None = None,
) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from Coinbase API responses.

    Args:
        portfolio_resp: Portfolio-level summary from Coinbase Advanced Trade.
        position_resps: All positions in this portfolio from Coinbase.
        route: A or B.
        expected_portfolio_id: If provided, validate that the response's
            portfolio_uuid matches. Raises PortfolioMismatchError on mismatch.
        realized_pnl_today_usdc: Today's realized P&L from our internal tracking.
        funding_pnl_today_usdc: Today's net funding from our tracker.
        fees_paid_today_usdc: Today's total fees from our fill records.
        now: Timestamp for the snapshot.
    """
    if expected_portfolio_id and portfolio_resp.portfolio_uuid:
        if portfolio_resp.portfolio_uuid != expected_portfolio_id:
            raise PortfolioMismatchError(
                expected_target=route.value,
                expected_id=expected_portfolio_id,
                actual_id=portfolio_resp.portfolio_uuid,
            )

    now = now or utc_now()
    positions = [
        build_position(pr, route) for pr in position_resps
    ]

    collateral = Decimal(portfolio_resp.collateral) if portfolio_resp.collateral else Decimal("0")
    total_balance = _amount_to_decimal(portfolio_resp.total_balance, collateral)
    unrealized_pnl = _amount_to_decimal(portfolio_resp.unrealized_pnl)
    used_margin = (
        Decimal(portfolio_resp.portfolio_initial_margin)
        if portfolio_resp.portfolio_initial_margin
        else Decimal("0")
    )
    # Available = total_balance - used_margin (approximation)
    available = total_balance - used_margin if total_balance > used_margin else Decimal("0")

    balance = total_balance if total_balance > 0 else collateral
    equity = balance + unrealized_pnl

    margin_util = (
        float(used_margin / equity * 100)
        if equity > 0
        else 0.0
    )

    return PortfolioSnapshot(
        timestamp=now,
        route=route,
        equity_usdc=equity,
        used_margin_usdc=used_margin,
        available_margin_usdc=available,
        margin_utilization_pct=margin_util,
        positions=positions,
        unrealized_pnl_usdc=unrealized_pnl,
        realized_pnl_today_usdc=realized_pnl_today_usdc,
        funding_pnl_today_usdc=funding_pnl_today_usdc,
        fees_paid_today_usdc=fees_paid_today_usdc,
    )


def build_system_snapshot(
    route_a: PortfolioSnapshot,
    route_b: PortfolioSnapshot,
    now: datetime | None = None,
) -> SystemSnapshot:
    """Combine both portfolio snapshots into a system-wide view."""
    return SystemSnapshot(
        timestamp=now or utc_now(),
        route_a=route_a,
        route_b=route_b,
    )


def _map_side(exchange_side: str, net_size: Decimal) -> PositionSide:
    """Map Coinbase side string + net size to our PositionSide enum.

    Advanced Trade returns prefixed values like POSITION_SIDE_LONG.
    """
    if net_size == 0:
        return PositionSide.FLAT
    side_upper = exchange_side.upper()
    if "LONG" in side_upper or side_upper == "BUY":
        return PositionSide.LONG
    if "SHORT" in side_upper or side_upper == "SELL":
        return PositionSide.SHORT
    # Fallback: infer from sign
    return PositionSide.LONG if net_size > 0 else PositionSide.SHORT
