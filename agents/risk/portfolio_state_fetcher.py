"""Fetch fresh portfolio state from Coinbase INTX REST API.

Every risk evaluation must use up-to-date equity and margin data.
This module wraps the client pool to build a PortfolioSnapshot from
the live Coinbase API, routing each request to the portfolio's own
API key via CoinbaseClientPool.
"""

from __future__ import annotations

from decimal import Decimal

from libs.coinbase.client_pool import CoinbaseClientPool
from libs.common.models.enums import PortfolioTarget, PositionSide
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import utc_now


class PortfolioStateFetcher:
    """Query Coinbase INTX for a portfolio's current state.

    Args:
        client_pool: Per-portfolio REST client pool.
    """

    def __init__(self, client_pool: CoinbaseClientPool) -> None:
        self._pool = client_pool

    async def fetch(self, target: PortfolioTarget) -> PortfolioSnapshot:
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

        positions = [
            PerpPosition(
                instrument=p.instrument_id,
                portfolio_target=target,
                side=PositionSide(p.side) if p.side in ("LONG", "SHORT") else PositionSide.FLAT,
                size=abs(p.net_size),
                entry_price=p.average_entry_price,
                mark_price=p.mark_price,
                unrealized_pnl_usdc=p.unrealized_pnl,
                realized_pnl_usdc=Decimal("0"),
                leverage=(
                    abs(p.net_size) * p.mark_price / portfolio_resp.total_equity
                    if portfolio_resp.total_equity > 0 and abs(p.net_size) > 0
                    else Decimal("0")
                ),
                initial_margin_usdc=p.initial_margin,
                maintenance_margin_usdc=p.maintenance_margin,
                liquidation_price=p.liquidation_price or Decimal("0"),
                margin_ratio=(
                    float(p.maintenance_margin / portfolio_resp.total_equity)
                    if portfolio_resp.total_equity > 0
                    else 0.0
                ),
                cumulative_funding_usdc=Decimal("0"),
                total_fees_usdc=Decimal("0"),
            )
            for p in positions_resp
        ]

        now = utc_now()
        return PortfolioSnapshot(
            timestamp=now,
            portfolio_target=target,
            equity_usdc=portfolio_resp.total_equity,
            used_margin_usdc=portfolio_resp.used_margin,
            available_margin_usdc=portfolio_resp.available_margin,
            margin_utilization_pct=(
                float(portfolio_resp.margin_utilization * 100)
                if portfolio_resp.margin_utilization
                else 0.0
            ),
            positions=positions,
            unrealized_pnl_usdc=portfolio_resp.unrealized_pnl,
            realized_pnl_today_usdc=Decimal("0"),
            funding_pnl_today_usdc=Decimal("0"),
            fees_paid_today_usdc=Decimal("0"),
        )
