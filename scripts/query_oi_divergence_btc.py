#!/usr/bin/env python
"""Query post-tuner OI Divergence BTC fills and print P&L metrics.

Connects to the production PostgreSQL database, fetches fills attributed to
the oi_divergence strategy for a given instrument since a configurable
start date, and prints key performance metrics.

Usage (from project root, venv active):
    DATABASE_URL=postgresql://... python scripts/query_oi_divergence_btc.py
    DATABASE_URL=postgresql://... python scripts/query_oi_divergence_btc.py --since 2025-01-01
    DATABASE_URL=postgresql://... python scripts/query_oi_divergence_btc.py --instrument ETH-PERP-INTX
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
)
logger = structlog.get_logger("query_oi_divergence_btc")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query post-tuner OI Divergence BTC fills and print P&L metrics."
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="Start date for fill query (default: 30 days ago)",
    )
    parser.add_argument(
        "--instrument",
        default="BTC-PERP-INTX",
        help="Instrument to query (default: BTC-PERP-INTX)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set.\n"
            "Example: DATABASE_URL=postgresql://user:pass@host:5432/dbname "
            "python scripts/query_oi_divergence_btc.py",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.since is not None:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            print(
                f"ERROR: --since value '{args.since}' is not in YYYY-MM-DD format.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(days=30)

    # Import after sys.path is set
    from libs.metrics.engine import compute_strategy_metrics
    from libs.storage.relational import RelationalStore
    from libs.storage.repository import TunerRepository

    logger.info(
        "query_oi_divergence_btc_starting",
        source="oi_divergence",
        instrument=args.instrument,
        since=since_dt.isoformat(),
    )

    store = RelationalStore(database_url)
    try:
        repo = TunerRepository(store)
        fills = await repo.get_fills_since(
            source="oi_divergence",
            instrument=args.instrument,
            since_dt=since_dt,
        )
    finally:
        await store.close()

    if not fills:
        print(
            f"No fills found for oi_divergence / {args.instrument} "
            f"since {since_dt.date().isoformat()}."
        )
        return

    logger.info("query_oi_divergence_btc_fills_fetched", fill_count=len(fills))

    metrics_map = compute_strategy_metrics(fills, min_trades=1)
    key = ("oi_divergence", args.instrument)
    metrics = metrics_map.get(key)

    if metrics is None:
        print(
            f"Insufficient data for metrics: {len(fills)} fill(s) found but no closed "
            f"round-trips for oi_divergence / {args.instrument} since "
            f"{since_dt.date().isoformat()}."
        )
        return

    print(
        f"\n{'='*60}\n"
        f"OI Divergence {args.instrument} — Post-Tuner P&L Summary\n"
        f"Since: {since_dt.date().isoformat()}\n"
        f"{'='*60}\n"
        f"  trade_count       : {metrics.trade_count}\n"
        f"  win_rate          : {metrics.win_rate:.1%}\n"
        f"  total_fees_usdc   : {metrics.total_fees_usdc:.4f}\n"
        f"  total_net_pnl     : {metrics.total_net_pnl:.4f}\n"
        f"  expectancy_usdc   : {metrics.expectancy_usdc:.4f}\n"
        f"{'='*60}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
