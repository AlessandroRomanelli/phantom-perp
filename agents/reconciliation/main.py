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

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.models import PortfolioResponse, PositionResponse
from libs.coinbase.rate_limiter import RateLimiter
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.common.config import AppSettings, get_settings
from libs.common.exceptions import CoinbaseAPIError, RateLimitExceededError
from libs.common.logging import setup_logging
from libs.common.models.enums import Route
from libs.common.models.position import PerpPosition
from libs.common.serialization import (
    deserialize_fill,
    deserialize_funding_payment,
    deserialize_portfolio_snapshot,
    funding_payment_to_dict,
    portfolio_snapshot_to_dict,
)
from libs.common.utils import utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisPublisher

from agents.reconciliation.state_manager import build_portfolio_snapshot

logger = setup_logging("reconciliation", json_output=False)

# How often to poll each portfolio (seconds)
POLL_INTERVAL = 30


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
                    "poll_route_backoff",
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


def _create_live_clients(
    settings: AppSettings,
) -> tuple[CoinbaseRESTClient, CoinbaseRESTClient]:
    """Create Route A and Route B REST clients from settings.

    Returns (client_a, client_b). When api_key_b is set, client_b uses separate
    Route B credentials. When api_key_b is empty, client_b is the same object as
    client_a (backward-compatible single-client fallback).
    """
    auth_a = CoinbaseAuth(
        api_key=settings.coinbase.api_key_a,
        api_secret=settings.coinbase.api_secret_a,
    )
    client_a = CoinbaseRESTClient(
        auth=auth_a,
        base_url=settings.coinbase.rest_url,
        rate_limiter=RateLimiter(),
    )

    if settings.coinbase.api_key_b and settings.coinbase.api_secret_b:
        auth_b = CoinbaseAuth(
            api_key=settings.coinbase.api_key_b,
            api_secret=settings.coinbase.api_secret_b,
        )
        client_b = CoinbaseRESTClient(
            auth=auth_b,
            base_url=settings.coinbase.rest_url,
            rate_limiter=RateLimiter(),
        )
        logger.info("route_b_credentials_loaded", note="using separate Route B API key")
    else:
        client_b = client_a
        logger.info("route_b_credentials_fallback", note="using Route A API key for Route B")

    return client_a, client_b


async def run_agent() -> None:
    """Main event loop for the reconciliation agent.

    In paper mode (ENVIRONMENT=paper): runs the paper trading simulator,
    which consumes approved orders, simulates fills, and publishes
    portfolio snapshots — all without touching the Coinbase API.

    In live mode: polls both Coinbase portfolios on a fixed interval,
    builds and publishes PortfolioSnapshot for each.
    """
    settings = get_settings()

    portfolio_id = settings.portfolios.portfolio_id
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
            portfolio_id=portfolio_id,
        )

        try:
            await run_paper_simulator(
                redis_url=settings.infra.redis_url,
                publisher=publisher,
                include_route_b=True,
                repo=repo,
            )
        finally:
            await db_store.close()
            await publisher.close()
            logger.info("reconciliation_agent_stopped", mode="paper")
        return

    # --- Live mode: poll Coinbase API ---
    client_a, client_b = _create_live_clients(settings)

    logger.info(
        "reconciliation_agent_started",
        mode="live",
        portfolio_id=portfolio_id,
        poll_interval=POLL_INTERVAL,
    )

    try:
        async with asyncio.TaskGroup() as tg:
            # Poll Route A with Route A credentials
            tg.create_task(
                run_portfolio_poller(
                    client_a, Route.A, publisher,
                    expected_portfolio_id=portfolio_id,
                ),
            )

            # Poll Route B — uses dedicated credentials when configured, else falls back to A
            tg.create_task(
                run_portfolio_poller(
                    client_b, Route.B, publisher,
                    expected_portfolio_id=portfolio_id,
                ),
            )

    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("reconciliation_task_failed", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await client_a.close()
        if client_b is not client_a:
            await client_b.close()
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
