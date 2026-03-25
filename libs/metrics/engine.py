"""Metrics engine: VWAP aggregation, FIFO round-trip pairing, and P&L computation.

Transforms raw AttributedFill rows (one per exchange fill) into closed RoundTrip
objects (one per completed trade) for performance metrics computation.

Pipeline:
    AttributedFill[] -> vwap_aggregate() -> OrderResult
    OrderResult[]    -> pair_round_trips() -> RoundTrip[]
    AttributedFill[] -> build_round_trips() -> dict[(source, instrument), RoundTrip[]]
    AttributedFill[] -> compute_strategy_metrics() -> dict[(source, instrument), StrategyMetrics | None]
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from libs.storage.repository import AttributedFill


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Aggregated result for a single order (potentially multiple partial fills).

    Partial fills for the same order_id are VWAP-aggregated into a single OrderResult.
    """

    order_id: str
    instrument: str
    primary_source: str
    side: str  # "BUY" or "SELL"
    avg_price: Decimal
    total_size: Decimal
    total_fee: Decimal
    filled_at: datetime  # latest fill timestamp


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A completed round-trip trade (entry + exit pairing).

    Entry and exit orders are paired via FIFO matching. Open positions
    (unmatched entries) are excluded per D-04.
    """

    entry_order_id: str
    exit_order_id: str
    instrument: str
    primary_source: str
    side: str  # entry side: "BUY" (LONG) or "SELL" (SHORT)
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    gross_pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    opened_at: datetime
    closed_at: datetime


def vwap_aggregate(fills: list[AttributedFill]) -> OrderResult:
    """VWAP-aggregate partial fills for a single order into an OrderResult.

    Filters zero-size fills before computing the weighted average to guard
    against ZeroDivisionError when partial fills have zero size.

    Args:
        fills: Non-empty list of fills (typically all for the same order_id).

    Returns:
        OrderResult with VWAP average price, summed size, and summed fees.
        The filled_at timestamp is the latest fill timestamp in the group.

    Raises:
        ValueError: If all fills have zero size after filtering.
    """
    assert len(fills) > 0, "fills must be non-empty"

    # Filter out zero-size fills to guard against ZeroDivisionError
    valid_fills = [f for f in fills if f.size > Decimal("0")]

    total_size = sum((f.size for f in valid_fills), Decimal("0"))
    if total_size == Decimal("0"):
        raise ValueError(
            f"All fills have zero size for order {fills[0].order_id}"
        )

    total_value = sum(f.price * f.size for f in valid_fills)
    avg_price = total_value / total_size

    # Sum fees across ALL fills (including zero-size -- fees may still apply)
    total_fee = sum((f.fee_usdc for f in fills), Decimal("0"))

    # Use the latest fill timestamp
    filled_at = max(f.filled_at for f in fills)

    first = fills[0]
    return OrderResult(
        order_id=first.order_id,
        instrument=first.instrument,
        primary_source=first.primary_source,
        side=first.side,
        avg_price=avg_price,
        total_size=total_size,
        total_fee=total_fee,
        filled_at=filled_at,
    )


def pair_round_trips(orders: list[OrderResult]) -> list[RoundTrip]:
    """Pair entry and exit orders into closed RoundTrip objects via FIFO matching.

    Direction logic:
    - If entry.side == "BUY" (LONG): gross_pnl = (exit_price - entry_price) * size
    - If entry.side == "SELL" (SHORT): gross_pnl = (entry_price - exit_price) * size

    A BUY order that closes a short position is correctly identified as an EXIT
    because the FIFO stack's top entry has side="SELL" != "BUY".

    Unmatched entries remaining in the stack are open positions and are excluded
    from the output per D-04.

    Args:
        orders: List of OrderResult objects. Caller must ensure chronological order
                (build_round_trips applies a defensive sort before calling this).

    Returns:
        List of closed RoundTrip objects.
    """
    entry_stack: deque[OrderResult] = deque()
    round_trips: list[RoundTrip] = []

    for order in orders:
        if not entry_stack or order.side == entry_stack[0].side:
            # No open position, or same side as existing entries: new entry (or pyramid)
            entry_stack.appendleft(order)
        else:
            # Opposite side: this order closes the oldest open entry (FIFO)
            entry = entry_stack.pop()  # pop from right = FIFO (oldest first)
            exit_order = order

            size = min(entry.total_size, exit_order.total_size)

            # Direction determines P&L formula:
            # SELL entry (SHORT): gross_pnl = (entry_price - exit_price) * size
            if entry.side == "SELL":
                gross_pnl = (entry.avg_price - exit_order.avg_price) * size
            else:
                # BUY entry (LONG): gross_pnl = (exit_price - entry_price) * size
                gross_pnl = (exit_order.avg_price - entry.avg_price) * size

            total_fees = entry.total_fee + exit_order.total_fee
            net_pnl = gross_pnl - total_fees

            round_trips.append(
                RoundTrip(
                    entry_order_id=entry.order_id,
                    exit_order_id=exit_order.order_id,
                    instrument=entry.instrument,
                    primary_source=entry.primary_source,
                    side=entry.side,
                    entry_price=entry.avg_price,
                    exit_price=exit_order.avg_price,
                    size=size,
                    gross_pnl=gross_pnl,
                    total_fees=total_fees,
                    net_pnl=net_pnl,
                    opened_at=entry.filled_at,
                    closed_at=exit_order.filled_at,
                )
            )

    # Remaining entries in the stack are open positions -- excluded per D-04
    return round_trips


def build_round_trips(
    fills: list[AttributedFill],
) -> dict[tuple[str, str], list[RoundTrip]]:
    """Build round-trips from raw fills, grouped by (primary_source, instrument).

    Pipeline:
    1. Group fills by (primary_source, instrument)
    2. Within each group, sub-group by order_id and VWAP-aggregate each sub-group
    3. Sort OrderResults by filled_at ascending (defensive sort for FIFO correctness)
    4. Pair into RoundTrips via pair_round_trips()

    Only groups with at least one closed round-trip are included in the result.

    Args:
        fills: List of AttributedFill records in any order.

    Returns:
        Dict keyed by (primary_source, instrument) with list[RoundTrip].
        Keys with no completed round-trips are excluded.
    """
    if not fills:
        return {}

    # Step 1: Group fills by (source, instrument)
    groups: dict[tuple[str, str], list[AttributedFill]] = defaultdict(list)
    for fill in fills:
        key = (fill.primary_source, fill.instrument)
        groups[key].append(fill)

    result: dict[tuple[str, str], list[RoundTrip]] = {}

    for key, group_fills in groups.items():
        # Step 2: Sub-group by order_id and VWAP-aggregate
        by_order: dict[str, list[AttributedFill]] = defaultdict(list)
        for fill in group_fills:
            by_order[fill.order_id].append(fill)

        orders: list[OrderResult] = [
            vwap_aggregate(order_fills) for order_fills in by_order.values()
        ]

        # Step 3: Defensive sort by filled_at ascending (FIFO correctness)
        orders.sort(key=lambda o: o.filled_at)

        # Step 4: Pair round-trips
        trips = pair_round_trips(orders)

        if trips:
            result[key] = trips

    return result


@dataclass(frozen=True, slots=True)
class StrategyMetrics:
    """Per-(strategy, instrument) performance summary.

    METR-04 note: funding_costs_usdc is Decimal("0") — funding cost attribution
    is deferred per D-08 (requires position lifecycle tracking not in Phase 10).
    total_net_pnl = total_gross_pnl - total_fees_usdc - funding_costs_usdc.
    """

    primary_source: str
    instrument: str
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float  # 0.0-1.0
    avg_win_usdc: Decimal
    avg_loss_usdc: Decimal
    expectancy_usdc: Decimal  # METR-01
    profit_factor: float | None  # METR-02; None if no losing trades
    total_gross_pnl: Decimal  # METR-04 gross (D-09)
    total_fees_usdc: Decimal  # METR-04 trading fees
    funding_costs_usdc: Decimal  # METR-04 funding costs (Decimal("0") per D-08)
    total_net_pnl: Decimal  # METR-04 net = gross - fees - funding (D-09)
    max_drawdown_usdc: Decimal  # METR-03 amount
    max_drawdown_duration_hours: float  # METR-03 duration (D-07)


def _compute_metrics(
    source: str,
    instrument: str,
    round_trips: list[RoundTrip],
) -> StrategyMetrics:
    """Compute StrategyMetrics from a list of closed round-trips.

    Args:
        source: Strategy name (primary_source value).
        instrument: Instrument identifier.
        round_trips: Non-empty list of closed round-trips (already sorted by closed_at).

    Returns:
        StrategyMetrics frozen dataclass with all METR-01 through METR-04 fields.
    """
    # --- Win/Loss classification (net_pnl basis per D-09) ---
    # Zero-P&L trades classified as losses (conservative -- fees make true breakeven negative)
    wins = [rt for rt in round_trips if rt.net_pnl > Decimal("0")]
    losses = [rt for rt in round_trips if rt.net_pnl <= Decimal("0")]
    trade_count = len(round_trips)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / trade_count  # float 0.0-1.0

    # --- Expectancy (METR-01) ---
    avg_win = (
        sum((rt.net_pnl for rt in wins), Decimal("0")) / Decimal(win_count)
        if wins
        else Decimal("0")
    )
    avg_loss = (
        sum((abs(rt.net_pnl) for rt in losses), Decimal("0")) / Decimal(loss_count)
        if losses
        else Decimal("0")
    )
    # Convert float rates to Decimal for monetary arithmetic (avoid Decimal * float)
    expectancy = avg_win * Decimal(str(win_rate)) - avg_loss * Decimal(str(1.0 - win_rate))

    # --- Profit Factor (METR-02) ---
    gross_profit = sum(
        (rt.gross_pnl for rt in round_trips if rt.gross_pnl > Decimal("0")),
        Decimal("0"),
    )
    gross_loss_raw = sum(
        (rt.gross_pnl for rt in round_trips if rt.gross_pnl < Decimal("0")),
        Decimal("0"),
    )
    gross_loss = abs(gross_loss_raw)
    # Breakeven trades (gross_pnl == 0) excluded from both sums
    profit_factor: float | None = (
        float(gross_profit / gross_loss) if gross_loss > Decimal("0") else None
    )

    # --- Drawdown (METR-03) ---
    # Sort defensively by closed_at (build_round_trips already sorts, but guard here)
    sorted_trips = sorted(round_trips, key=lambda rt: rt.closed_at)
    cumulative = Decimal("0")
    peak = Decimal("0")
    peak_time = sorted_trips[0].closed_at
    max_dd_amount = Decimal("0")
    max_dd_duration_hours: float = 0.0

    for rt in sorted_trips:
        cumulative += rt.net_pnl
        if cumulative > peak:
            peak = cumulative
            peak_time = rt.closed_at
        dd = peak - cumulative
        if dd > max_dd_amount:
            max_dd_amount = dd
            # Duration from peak to trough (current trade's close time)
            max_dd_duration_hours = (rt.closed_at - peak_time).total_seconds() / 3600

    # After loop: check if still in drawdown (D-07: use current time if no recovery)
    if cumulative < peak:
        still_dd_duration = (datetime.now(timezone.utc) - peak_time).total_seconds() / 3600
        if still_dd_duration > max_dd_duration_hours:
            max_dd_duration_hours = still_dd_duration

    # --- Fee-adjusted aggregates (METR-04/D-09) with funding placeholder (D-08) ---
    total_gross_pnl = sum((rt.gross_pnl for rt in round_trips), Decimal("0"))
    total_fees_usdc = sum((rt.total_fees for rt in round_trips), Decimal("0"))
    funding_costs_usdc = Decimal("0")  # D-08: deferred to METR-05/06
    total_net_pnl = total_gross_pnl - total_fees_usdc - funding_costs_usdc

    return StrategyMetrics(
        primary_source=source,
        instrument=instrument,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        avg_win_usdc=avg_win,
        avg_loss_usdc=avg_loss,
        expectancy_usdc=expectancy,
        profit_factor=profit_factor,
        total_gross_pnl=total_gross_pnl,
        total_fees_usdc=total_fees_usdc,
        funding_costs_usdc=funding_costs_usdc,
        total_net_pnl=total_net_pnl,
        max_drawdown_usdc=max_dd_amount,
        max_drawdown_duration_hours=max_dd_duration_hours,
    )


def compute_strategy_metrics(
    fills: list[AttributedFill],
    min_trades: int = 10,
) -> dict[tuple[str, str], StrategyMetrics | None]:
    """Compute per-(strategy, instrument) performance metrics from attributed fills.

    Groups fills by (primary_source, instrument), reconstructs closed round-trips,
    applies the minimum-count gate (D-01/D-02), and returns StrategyMetrics for
    pairs with sufficient data, or None for pairs below the minimum threshold.

    Args:
        fills: List of attributed fill records (typically from TunerRepository).
        min_trades: Minimum number of closed round-trips required to compute metrics.
                    Pairs below this threshold return None (D-01/D-02).

    Returns:
        Dict keyed by (primary_source, instrument). Value is StrategyMetrics if
        len(round_trips) >= min_trades, else None.
    """
    if not fills:
        return {}

    round_trips_by_key = build_round_trips(fills)

    # Also include keys from fills that had no closed round-trips (for gate tracking)
    # We need to find all (source, instrument) pairs that appear in fills
    all_keys: set[tuple[str, str]] = set()
    for fill in fills:
        all_keys.add((fill.primary_source, fill.instrument))

    result: dict[tuple[str, str], StrategyMetrics | None] = {}
    for key in all_keys:
        trips = round_trips_by_key.get(key, [])
        if len(trips) < min_trades:
            result[key] = None
        else:
            source, instrument = key
            result[key] = _compute_metrics(source, instrument, trips)

    return result
