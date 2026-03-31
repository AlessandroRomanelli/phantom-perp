"""Fetch fresh portfolio state from Coinbase Advanced REST API.

Every risk evaluation must use up-to-date equity and margin data.
This module wraps the client pool to build a PortfolioSnapshot from
the live Coinbase API, routing each request to the portfolio's own
API key via CoinbaseClientPool.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from libs.coinbase.client_pool import CoinbaseClientPool
from libs.common.models.enums import Route, PositionSide
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import utc_now

log = structlog.get_logger(__name__)


class PortfolioStateFetcher:
    """Query Coinbase Advanced for a portfolio's current state.

    Args:
        client_pool: Per-portfolio REST client pool.
    """

    def __init__(self, client_pool: CoinbaseClientPool) -> None:
        self._pool = client_pool
        log.warning(
            "live_pnl_fields_unavailable",
            detail=(
                "Coinbase /intx/portfolio endpoint does not provide "
                "realized_pnl_today, funding_pnl_today, or fees_paid_today. "
                "These fields are hardcoded to 0 in live mode; daily loss and "
                "drawdown kill switches rely on the monitoring agent's tracking."
            ),
        )

    async def fetch(self, target: Route) -> PortfolioSnapshot:
        """Fetch live portfolio state from Coinbase.

        Args:
            target: Which portfolio to query (A or B).

        Returns:
            A PortfolioSnapshot built from live API data.

        Raises:
            CoinbaseAPIError: On API failure.
        """
        client = self._pool.get_client(target)

        portfolio_resp = await client.get_portfolio()

        positions_resp = await client.get_positions()

        position_count = len(positions_resp) if positions_resp else 1
        maint_margin_per_pos = (
            Decimal(portfolio_resp.portfolio_maintenance_margin) / position_count
            if position_count > 0
            else Decimal("0")
        )

        positions = [
            PerpPosition(
                instrument=p.product_id,
                route=target,
                side=PositionSide(p.position_side) if p.position_side in ("LONG", "SHORT") else PositionSide.FLAT,
                size=abs(Decimal(p.net_size)),
                entry_price=Decimal(p.entry_vwap.value) if p.entry_vwap else Decimal("0"),
                mark_price=Decimal(p.mark_price.value) if p.mark_price else Decimal("0"),
                unrealized_pnl_usdc=Decimal(p.unrealized_pnl.value) if p.unrealized_pnl else Decimal("0"),
                realized_pnl_usdc=Decimal("0"),
                leverage=(
                    abs(Decimal(p.net_size)) * Decimal(p.mark_price.value) / Decimal(portfolio_resp.collateral)
                    if Decimal(portfolio_resp.collateral) > 0 and abs(Decimal(p.net_size)) > 0 and p.mark_price
                    else Decimal("0")
                ),
                initial_margin_usdc=Decimal(p.im_contribution),
                maintenance_margin_usdc=maint_margin_per_pos,
                liquidation_price=Decimal(p.liquidation_price.value) if p.liquidation_price else Decimal("0"),
                margin_ratio=(
                    float(maint_margin_per_pos / Decimal(portfolio_resp.collateral))
                    if Decimal(portfolio_resp.collateral) > 0
                    else 0.0
                ),
                cumulative_funding_usdc=Decimal("0"),
                total_fees_usdc=Decimal("0"),
            )
            for p in positions_resp
        ]

        equity = Decimal(portfolio_resp.collateral)
        used_margin = Decimal(portfolio_resp.portfolio_initial_margin)
        available_margin = equity - used_margin
        margin_utilization = used_margin / equity if equity > 0 else Decimal("0")
        unrealized_pnl = Decimal(portfolio_resp.unrealized_pnl.value) if portfolio_resp.unrealized_pnl else Decimal("0")

        now = utc_now()
        return PortfolioSnapshot(
            timestamp=now,
            route=target,
            equity_usdc=equity,
            used_margin_usdc=used_margin,
            available_margin_usdc=available_margin,
            margin_utilization_pct=float(margin_utilization * 100),
            positions=positions,
            unrealized_pnl_usdc=unrealized_pnl,
            # Coinbase /intx/portfolio does not expose daily realized P&L,
            # funding P&L, or fee totals. These remain zero in live mode;
            # the monitoring agent tracks them from fills and funding events.
            realized_pnl_today_usdc=Decimal("0"),
            funding_pnl_today_usdc=Decimal("0"),
            fees_paid_today_usdc=Decimal("0"),
        )
