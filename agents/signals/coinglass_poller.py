"""Coinglass liquidation heatmap poller.

Polls the Coinglass heatmap endpoint on a configurable cadence and
maintains a shared in-memory dict of ``LiquidationCluster`` lists —
one entry per instrument.  The ``LiquidationCascadeStrategy`` reads from
this dict on each ``evaluate()`` tick.

Design notes:
- One poller task covers all instruments sequentially each wake cycle.
- Per-instrument errors are caught and logged — one bad call does not
  halt others (graceful degradation).
- When an instrument has no snapshot in ``latest_snapshots`` its current
  price is unknown, so we skip it rather than compute nonsensical distances.
- The shared dict is mutated in-place; callers read ``latest_heatmaps``
  without taking a lock (Python GIL makes dict updates effectively atomic
  for top-level key assignment).

Follows the same structural pattern as ``run_claude_scheduler()`` in
``agents/signals/claude_scheduler.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agents.signals.coinglass_client import CoinglassClient
from libs.common.exceptions import CoinglassAPIError

if TYPE_CHECKING:
    from libs.common.models.market_snapshot import MarketSnapshot

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiquidationCluster:
    """A single liquidation cluster at a given price level.

    Attributes:
        price_level: Price level (in quote currency) where the cluster sits.
        notional_usd: Aggregated notional value of liquidations at this level.
        distance_pct: Absolute distance from the current price as a percentage.
    """

    price_level: float
    notional_usd: float
    distance_pct: float


# ---------------------------------------------------------------------------
# Instrument → Coinglass symbol mapping
# ---------------------------------------------------------------------------

INSTRUMENT_TO_CG_SYMBOL: dict[str, str] = {
    "ETH-PERP": "ETH",
    "BTC-PERP": "BTC",
    "SOL-PERP": "SOL",
}

# Maximum number of top clusters to keep per instrument
_MAX_CLUSTERS: int = 10


# ---------------------------------------------------------------------------
# Heatmap parser
# ---------------------------------------------------------------------------


def parse_heatmap_response(
    raw: dict,
    current_price: float,
    min_notional_usd: float = 0.0,
) -> list[LiquidationCluster]:
    """Parse a Coinglass heatmap API ``data`` dict into ``LiquidationCluster`` objects.

    The heatmap response has this structure::

        {
            "y_axis": [price_level_0, price_level_1, ...],
            "liquidation_leverage_data": [
                [x_idx, y_idx, notional_usd],
                ...
            ]
        }

    ``x_idx`` is the time bucket index (ignored here — we want the static
    price distribution).  ``y_idx`` indexes into ``y_axis``.  Multiple
    triplets may share the same ``y_idx`` (different leverage buckets) and
    are aggregated by summing notional.

    Args:
        raw: The ``data`` field of a Coinglass heatmap API response.
        current_price: Current mark/last price for distance computation.
        min_notional_usd: Minimum aggregated notional to include a cluster.
            Clusters below this threshold are filtered out.

    Returns:
        Up to ``_MAX_CLUSTERS`` (10) ``LiquidationCluster`` objects sorted
        descending by notional.  Returns an empty list if data is missing
        or malformed.
    """
    y_axis: list[float] = raw.get("y_axis") or []
    triplets: list[list] = raw.get("liquidation_leverage_data") or []

    if not y_axis or not triplets:
        return []

    # Aggregate notional per y_idx (price level bucket)
    notional_by_y: dict[int, float] = {}
    for triplet in triplets:
        if len(triplet) < 3:
            continue
        try:
            y_idx = int(triplet[1])
            notional = float(triplet[2])
        except (TypeError, ValueError):
            continue
        if 0 <= y_idx < len(y_axis):
            notional_by_y[y_idx] = notional_by_y.get(y_idx, 0.0) + notional

    if not notional_by_y:
        return []

    clusters: list[LiquidationCluster] = []
    for y_idx, total_notional in notional_by_y.items():
        if total_notional < min_notional_usd:
            continue
        price_level = float(y_axis[y_idx])
        distance_pct = (
            abs(price_level - current_price) / current_price * 100.0
            if current_price > 0
            else 0.0
        )
        clusters.append(
            LiquidationCluster(
                price_level=price_level,
                notional_usd=total_notional,
                distance_pct=distance_pct,
            )
        )

    # Sort descending by notional, keep top _MAX_CLUSTERS
    clusters.sort(key=lambda c: c.notional_usd, reverse=True)
    return clusters[:_MAX_CLUSTERS]


# ---------------------------------------------------------------------------
# Async poller
# ---------------------------------------------------------------------------


async def run_coinglass_poller(
    instrument_ids: list[str],
    latest_heatmaps: dict[str, list[LiquidationCluster]],
    latest_snapshots: dict[str, "MarketSnapshot"],
    api_key: str,
    poll_interval: int = 300,
    min_notional_usd: float = 500_000.0,
) -> None:
    """Async poller that refreshes liquidation heatmap data for all instruments.

    Loops forever, waking every ``poll_interval`` seconds.  For each
    instrument that has:

    1. A Coinglass symbol mapping in ``INSTRUMENT_TO_CG_SYMBOL``, and
    2. A current ``MarketSnapshot`` in ``latest_snapshots`` (needed for the
       ``current_price`` used in distance computation),

    the poller calls the Coinglass API, parses the response, and updates
    ``latest_heatmaps[instrument_id]`` in-place.  On error the existing
    entry is left intact (graceful degradation).

    Args:
        instrument_ids: Instruments to poll (e.g. ``["ETH-PERP", ...]``).
        latest_heatmaps: Shared mutable dict updated by this poller.
            Downstream strategy instances read from this dict.
        latest_snapshots: Shared mutable dict maintained by the signals
            agent main loop.  Provides ``last_price`` for distance calc.
        api_key: Coinglass API key.
        poll_interval: Seconds to sleep between full poll cycles.
        min_notional_usd: Minimum cluster notional filter passed to the
            parser (skips low-value clusters to keep the dict lean).
    """
    _logger.info(
        "coinglass_poller_started",
        instruments=instrument_ids,
        poll_interval=poll_interval,
        min_notional_usd=min_notional_usd,
    )

    async with CoinglassClient(api_key=api_key) as client:
        while True:
            for instrument_id in instrument_ids:
                cg_symbol = INSTRUMENT_TO_CG_SYMBOL.get(instrument_id)
                if cg_symbol is None:
                    # No mapping — skip silently (e.g. QQQ-PERP, SPY-PERP)
                    continue

                snapshot = latest_snapshots.get(instrument_id)
                if snapshot is None:
                    _logger.debug(
                        "coinglass_poller_no_snapshot",
                        instrument=instrument_id,
                    )
                    continue

                current_price = float(snapshot.last_price)

                try:
                    raw = await client.get_liquidation_heatmap(cg_symbol)
                    clusters = parse_heatmap_response(
                        raw,
                        current_price=current_price,
                        min_notional_usd=min_notional_usd,
                    )
                    latest_heatmaps[instrument_id] = clusters
                    _logger.info(
                        "coinglass_poll_success",
                        instrument=instrument_id,
                        symbol=cg_symbol,
                        cluster_count=len(clusters),
                    )
                except (CoinglassAPIError, Exception) as exc:
                    _logger.warning(
                        "coinglass_poll_failed",
                        instrument=instrument_id,
                        symbol=cg_symbol,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )
                    # Leave existing data intact — graceful degradation

            await asyncio.sleep(poll_interval)
