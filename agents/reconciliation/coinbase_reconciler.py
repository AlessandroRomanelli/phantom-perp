"""Cross-check internal state vs Coinbase exchange state.

Detects discrepancies between what we think we have (from fills/events)
and what Coinbase actually reports (positions, orders, equity).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.coinbase.models import OrderResponse, PositionResponse
from libs.common.models.enums import Route, PositionSide
from libs.common.models.order import Fill


@dataclass(frozen=True, slots=True)
class PositionDiscrepancy:
    """A mismatch between internal and exchange position state."""

    route: Route
    instrument: str
    field: str
    internal_value: str
    exchange_value: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of a reconciliation check."""

    route: Route
    is_consistent: bool
    discrepancies: list[PositionDiscrepancy]
    orphaned_exchange_orders: list[str]


def reconcile_positions(
    internal_fills: list[Fill],
    exchange_positions: list[PositionResponse],
    route: Route,
    tolerance: Decimal = Decimal("0.001"),
) -> ReconciliationResult:
    """Compare internally tracked fills against exchange-reported positions.

    Args:
        internal_fills: All fills we've recorded for this portfolio.
        exchange_positions: Current positions reported by Coinbase.
        route: Which route to reconcile.
        tolerance: Acceptable size difference (for floating-point rounding).

    Returns:
        ReconciliationResult indicating consistency and any discrepancies.
    """
    # Build net internal position from fills
    internal_net = _compute_net_position(internal_fills)

    discrepancies: list[PositionDiscrepancy] = []

    for pos in exchange_positions:
        instrument = pos.product_id
        net_size = Decimal(pos.net_size) if pos.net_size else Decimal("0")
        exchange_size = abs(net_size)
        exchange_side = _infer_side(pos.position_side, net_size)

        internal_size = internal_net.get(instrument, Decimal("0"))
        internal_side = (
            PositionSide.LONG if internal_size > 0
            else PositionSide.SHORT if internal_size < 0
            else PositionSide.FLAT
        )
        internal_abs = abs(internal_size)

        # Check side
        if exchange_size > 0 and internal_abs > 0 and exchange_side != internal_side:
            discrepancies.append(PositionDiscrepancy(
                route=route,
                instrument=instrument,
                field="side",
                internal_value=internal_side.value,
                exchange_value=exchange_side.value,
            ))

        # Check size
        size_diff = abs(exchange_size - internal_abs)
        if size_diff > tolerance:
            discrepancies.append(PositionDiscrepancy(
                route=route,
                instrument=instrument,
                field="size",
                internal_value=str(internal_abs),
                exchange_value=str(exchange_size),
            ))

    return ReconciliationResult(
        route=route,
        is_consistent=len(discrepancies) == 0,
        discrepancies=discrepancies,
        orphaned_exchange_orders=[],
    )


def find_orphaned_orders(
    internal_order_ids: set[str],
    exchange_orders: list[OrderResponse],
) -> list[str]:
    """Find exchange orders that we don't have in our internal tracking.

    These might be orders placed manually or from a previous session.
    """
    return [
        o.order_id for o in exchange_orders
        if o.client_order_id not in internal_order_ids
        and o.order_id not in internal_order_ids
    ]


def _compute_net_position(fills: list[Fill]) -> dict[str, Decimal]:
    """Compute net position size from fills (positive=LONG, negative=SHORT)."""
    positions: dict[str, Decimal] = {}
    for fill in fills:
        instrument = fill.instrument
        size = fill.size
        if fill.side.value == "SELL":
            size = -size
        positions[instrument] = positions.get(instrument, Decimal("0")) + size
    return positions


def _infer_side(exchange_side: str, net_size: Decimal) -> PositionSide:
    """Infer position side from exchange value.

    Advanced Trade returns prefixed values like POSITION_SIDE_LONG.
    """
    if net_size == 0:
        return PositionSide.FLAT
    side_upper = exchange_side.upper()
    if "LONG" in side_upper or side_upper == "BUY":
        return PositionSide.LONG
    if "SHORT" in side_upper or side_upper == "SELL":
        return PositionSide.SHORT
    return PositionSide.LONG if net_size > 0 else PositionSide.SHORT
