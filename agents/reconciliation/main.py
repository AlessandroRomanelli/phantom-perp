"""Reconciliation agent — queries Coinbase portfolios and publishes state.

Periodically polls the Coinbase Advanced REST API for each portfolio's
equity, margin, and positions, then publishes PortfolioSnapshot objects
to stream:portfolio_state:a and stream:portfolio_state:b.

Subscribes to:
  - stream:exchange_events:a  (fills from execution, Route A)
  - stream:exchange_events:b  (fills from execution, Route B)

Publishes to:
  - stream:portfolio_state:a  (PortfolioSnapshot for A)
  - stream:portfolio_state:b  (PortfolioSnapshot for B)
  - stream:funding_payments:a (hourly funding for A)
  - stream:funding_payments:b (hourly funding for B)
  - stream:alerts             (discrepancy alerts)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.models import PortfolioResponse, PositionResponse
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.config import get_settings
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    OrderSide,
    Route,
    PositionSide,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisPublisher

from agents.reconciliation.state_manager import build_portfolio_snapshot

logger = setup_logging("reconciliation", json_output=False)

# How often to poll each portfolio (seconds)
POLL_INTERVAL = 30


# ---------------------------------------------------------------------------
# Serialization helpers (kept here for backward compat with existing tests)
# ---------------------------------------------------------------------------


def deserialize_fill(payload: dict[str, Any]) -> Fill:
    """Reconstruct a Fill from stream:exchange_events payload."""
    return Fill(
        fill_id=payload["fill_id"],
        order_id=payload["order_id"],
        route=Route(payload["route"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        price=Decimal(payload["price"]),
        fee_usdc=Decimal(payload["fee_usdc"]),
        is_maker=payload["is_maker"] == "True" if isinstance(payload["is_maker"], str) else bool(payload["is_maker"]),
        filled_at=datetime.fromisoformat(payload["filled_at"]),
        trade_id=payload["trade_id"],
    )


def portfolio_snapshot_to_dict(snap: PortfolioSnapshot) -> dict[str, Any]:
    """Serialize a PortfolioSnapshot for stream:portfolio_state:*."""
    return {
        "timestamp": snap.timestamp.isoformat(),
        "route": snap.route.value,
        "equity_usdc": str(snap.equity_usdc),
        "used_margin_usdc": str(snap.used_margin_usdc),
        "available_margin_usdc": str(snap.available_margin_usdc),
        "margin_utilization_pct": snap.margin_utilization_pct,
        "unrealized_pnl_usdc": str(snap.unrealized_pnl_usdc),
        "realized_pnl_today_usdc": str(snap.realized_pnl_today_usdc),
        "funding_pnl_today_usdc": str(snap.funding_pnl_today_usdc),
        "fees_paid_today_usdc": str(snap.fees_paid_today_usdc),
        "position_count": len(snap.open_positions),
        "positions": [
            {
                "instrument": p.instrument,
                "side": p.side.value,
                "size": str(p.size),
                "entry_price": str(p.entry_price.quantize(Decimal("0.01"))),
                "mark_price": str(p.mark_price.quantize(Decimal("0.01"))),
                "unrealized_pnl_usdc": str(p.unrealized_pnl_usdc.quantize(Decimal("0.01"))),
                "leverage": str(p.leverage),
                "liquidation_price": str(p.liquidation_price.quantize(Decimal("0.01"))),
            }
            for p in snap.positions
            if p.size > 0
        ],
    }


def deserialize_portfolio_snapshot(payload: dict[str, Any]) -> PortfolioSnapshot:
    """Reconstruct a PortfolioSnapshot from stream:portfolio_state payload."""
    return PortfolioSnapshot(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        route=Route(payload["route"]),
        equity_usdc=Decimal(payload["equity_usdc"]),
        used_margin_usdc=Decimal(payload["used_margin_usdc"]),
        available_margin_usdc=Decimal(payload["available_margin_usdc"]),
        margin_utilization_pct=float(payload["margin_utilization_pct"]),
        positions=[],
        unrealized_pnl_usdc=Decimal(payload["unrealized_pnl_usdc"]),
        realized_pnl_today_usdc=Decimal(payload["realized_pnl_today_usdc"]),
        funding_pnl_today_usdc=Decimal(payload["funding_pnl_today_usdc"]),
        fees_paid_today_usdc=Decimal(payload["fees_paid_today_usdc"]),
    )


def funding_payment_to_dict(payment: FundingPayment) -> dict[str, Any]:
    """Serialize a FundingPayment for stream:funding_payments:*."""
    return {
        "timestamp": payment.timestamp.isoformat(),
        "instrument": payment.instrument,
        "route": payment.route.value,
        "rate": str(payment.rate),
        "payment_usdc": str(payment.payment_usdc),
        "position_size": str(payment.position_size),
        "position_side": payment.position_side.value,
        "cumulative_24h_usdc": str(payment.cumulative_24h_usdc),
    }


def deserialize_funding_payment(payload: dict[str, Any]) -> FundingPayment:
    """Reconstruct a FundingPayment from stream:funding_payments payload."""
    return FundingPayment(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        route=Route(payload["route"]),
        rate=Decimal(payload["rate"]),
        payment_usdc=Decimal(payload["payment_usdc"]),
        position_size=Decimal(payload["position_size"]),
        position_side=PositionSide(payload["position_side"]),
        cumulative_24h_usdc=Decimal(payload["cumulative_24h_usdc"]),
    )


# ---------------------------------------------------------------------------
# Portfolio polling
# ---------------------------------------------------------------------------


async def poll_portfolio(
    client: CoinbaseRESTClient,
    target: Route,
    publisher: RedisPublisher,
    *,
    expected_portfolio_id: str | None = None,
) -> None:
    """Query Coinbase for a single portfolio's state and publish a snapshot.

    Fetches portfolio summary and positions, builds a PortfolioSnapshot,
    and publishes it to stream:portfolio_state:{a|b}.
    """
    try:
        portfolio_resp = await client.get_portfolio()
    except (CoinbaseAPIError, RateLimitExceededError) as e:
        logger.warning("portfolio_fetch_failed", portfolio=target.value, error=str(e))
        return
    except Exception as e:
        logger.error("portfolio_fetch_error", portfolio=target.value, error=str(e))
        return

    try:
        position_resps = await client.get_positions()
    except (CoinbaseAPIError, RateLimitExceededError) as e:
        logger.warning(
            "positions_fetch_failed",
            portfolio=target.value,
            error=str(e),
            msg="Skipping snapshot publish — positions data unavailable",
        )
        return
    except Exception as e:
        logger.error(
            "positions_fetch_error",
            portfolio=target.value,
            error=str(e),
            msg="Skipping snapshot publish — positions data unavailable",
        )
        return

    try:
        snapshot = build_portfolio_snapshot(
            portfolio_resp=portfolio_resp,
            position_resps=position_resps,
            route=target,
            expected_portfolio_id=expected_portfolio_id,
        )
    except Exception as e:
        logger.error(
            "snapshot_build_failed",
            portfolio=target.value,
            error=str(e),
            exc_type=type(e).__name__,
        )
        return

    channel = Channel.portfolio_state(target)
    payload = portfolio_snapshot_to_dict(snapshot)
    try:
        await publisher.publish(channel, payload)
    except Exception as e:
        logger.error(
            "snapshot_publish_failed",
            portfolio=target.value,
            error=str(e),
            exc_type=type(e).__name__,
        )
        return

    position_count = len(snapshot.open_positions)
    logger.info(
        "portfolio_snapshot_published",
        portfolio=target.value,
        equity_usdc=str(snapshot.equity_usdc),
        used_margin=str(snapshot.used_margin_usdc),
        margin_pct=f"{snapshot.margin_utilization_pct:.1f}",
        unrealized_pnl=str(snapshot.unrealized_pnl_usdc),
        positions=position_count,
    )


async def run_portfolio_poller(
    client: CoinbaseRESTClient,
    target: Route,
    publisher: RedisPublisher,
    *,
    expected_portfolio_id: str | None = None,
) -> None:
    """Continuously poll a single portfolio at a fixed interval."""
    label = target.value
    logger.info("portfolio_poller_started", portfolio=label, interval=POLL_INTERVAL)
    consecutive_failures = 0

    while True:
        try:
            await poll_portfolio(
                client, target, publisher, expected_portfolio_id=expected_portfolio_id,
            )
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            logger.error(
                "poll_portfolio_unexpected_error",
                portfolio=label,
                error=str(e),
                exc_type=type(e).__name__,
                consecutive_failures=consecutive_failures,
            )
            # Back off on repeated failures to avoid tight error loops
            if consecutive_failures >= 5:
                backoff = min(consecutive_failures * POLL_INTERVAL, 300)
                logger.warning(
                    "poll_portfolio_backoff",
                    portfolio=label,
                    backoff_seconds=backoff,
                    consecutive_failures=consecutive_failures,
                )
                await asyncio.sleep(backoff)
                continue
        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------


async def run_agent() -> None:
    """Main event loop for the reconciliation agent.

    In paper mode (ENVIRONMENT=paper): runs the paper trading simulator,
    which consumes approved orders, simulates fills, and publishes
    portfolio snapshots — all without touching the Coinbase API.

    In live mode: polls both Coinbase portfolios on a fixed interval,
    builds and publishes PortfolioSnapshot for each.
    """
    settings = get_settings()

    portfolio_a_id = settings.portfolios.portfolio_a_id
    portfolio_b_id = settings.portfolios.portfolio_b_id
    is_paper = settings.infra.environment == "paper"

    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    if is_paper:
        from agents.reconciliation.paper_simulator import run_paper_simulator

        from libs.storage.relational import RelationalStore, init_db
        from libs.storage.repository import TunerRepository

        # Initialize PostgreSQL storage for fill record persistence
        db_store = RelationalStore(settings.infra.database_url)
        await init_db(db_store.engine)
        repo = TunerRepository(db_store)
        logger.info("reconciliation_db_initialized", mode="paper")

        logger.info(
            "reconciliation_agent_started",
            mode="paper",
            portfolio_a_id=portfolio_a_id,
            portfolio_b_id=portfolio_b_id or "(not configured)",
        )

        try:
            await run_paper_simulator(
                redis_url=settings.infra.redis_url,
                publisher=publisher,
                include_portfolio_b=bool(portfolio_b_id),
                repo=repo,
            )
        finally:
            await db_store.close()
            await publisher.close()
            logger.info("reconciliation_agent_stopped", mode="paper")
        return

    # --- Live mode: poll Coinbase API ---
    auth_a = CoinbaseAuth(
        api_key=settings.coinbase.api_key_a,
        api_secret=settings.coinbase.api_secret_a,
    )

    client_a = CoinbaseRESTClient(
        auth=auth_a,
        base_url=settings.coinbase.rest_url,
        rate_limiter=RateLimiter(),
    )

    logger.info(
        "reconciliation_agent_started",
        mode="live",
        portfolio_a_id=portfolio_a_id,
        portfolio_b_id=portfolio_b_id or "(not configured)",
        poll_interval=POLL_INTERVAL,
    )

    try:
        async with asyncio.TaskGroup() as tg:
            # Always poll Route A
            tg.create_task(
                run_portfolio_poller(
                    client_a, Route.A, publisher,
                    expected_portfolio_id=portfolio_a_id,
                ),
            )

            # Poll Route B only if configured with its own dedicated API key
            if portfolio_b_id:
                if settings.coinbase.api_key_b:
                    auth_b = CoinbaseAuth(
                        api_key=settings.coinbase.api_key_b,
                        api_secret=settings.coinbase.api_secret_b,
                    )
                    client_b = CoinbaseRESTClient(
                        auth=auth_b,
                        base_url=settings.coinbase.rest_url,
                        rate_limiter=RateLimiter(),
                    )
                    tg.create_task(
                        run_portfolio_poller(
                            client_b, Route.B, publisher,
                            expected_portfolio_id=portfolio_b_id,
                        ),
                    )
                else:
                    logger.critical(
                        "portfolio_b_missing_api_key",
                        msg="Route B ID is configured but API key is missing. "
                        "Refusing to start Route B poller — would query Route A data.",
                        portfolio_b_id=portfolio_b_id,
                    )
            else:
                logger.info("portfolio_b_not_configured", msg="skipping Route B polling")

    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("reconciliation_task_failed", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await client_a.close()
        await publisher.close()
        logger.info("reconciliation_agent_stopped", mode="live")


def main() -> None:
    """CLI entrypoint."""
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass

    import sys
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("reconciliation_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
