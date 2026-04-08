"""Redis stream serialization/deserialization for all shared models.

One function per direction per model. Agents must NOT define local
copies — import from here.

Boolean parsing note:
  Redis stores booleans as str(bool) -> "True"/"False".
  _parse_bool() accepts "true", "True", "1", True, 1 — and raises
  ValueError on unrecognised input.  BUG-03 (Phase 17) will expand
  coverage further.

Functions grouped by model:
  MarketSnapshot  -> snapshot_to_dict / deserialize_snapshot
  StandardSignal  -> signal_to_dict / deserialize_signal
  RankedTradeIdea -> idea_to_dict / deserialize_idea
  ProposedOrder   -> order_to_dict / deserialize_proposed_order
  ApprovedOrder   -> approved_order_to_dict / deserialize_approved_order
  Fill            -> fill_to_dict / deserialize_fill
  PortfolioSnapshot -> portfolio_snapshot_to_dict / deserialize_portfolio_snapshot
  FundingPayment  -> funding_payment_to_dict / deserialize_funding_payment
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    Route,
    SignalSource,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_bool(value: str | bool | int) -> bool:
    """Parse a boolean from a Redis stream value.

    Accepts the output of str(True) / str(False) as well as common variants.
    Raises ValueError on unrecognised input.

    Args:
        value: The raw value from a Redis stream payload field.

    Returns:
        The parsed boolean value.

    Raises:
        ValueError: If the value cannot be interpreted as a boolean.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lower = value.lower()
        if lower in ("true", "1"):
            return True
        if lower in ("false", "0"):
            return False
    raise ValueError(f"Cannot parse bool from {value!r}")


# ---------------------------------------------------------------------------
# MarketSnapshot
# ---------------------------------------------------------------------------


def snapshot_to_dict(snapshot: MarketSnapshot) -> dict[str, Any]:
    """Serialize a MarketSnapshot to a JSON-compatible dict for Redis.

    Converts Decimal fields to strings and datetime to ISO 8601.

    Args:
        snapshot: The MarketSnapshot to serialize.

    Returns:
        Dict ready for orjson serialization.
    """
    return {
        "timestamp": snapshot.timestamp.isoformat(),
        "instrument": snapshot.instrument,
        "mark_price": str(snapshot.mark_price),
        "index_price": str(snapshot.index_price),
        "last_price": str(snapshot.last_price),
        "best_bid": str(snapshot.best_bid),
        "best_ask": str(snapshot.best_ask),
        "spread_bps": snapshot.spread_bps,
        "volume_24h": str(snapshot.volume_24h),
        "open_interest": str(snapshot.open_interest),
        "funding_rate": str(snapshot.funding_rate),
        "next_funding_time": snapshot.next_funding_time.isoformat(),
        "hours_since_last_funding": snapshot.hours_since_last_funding,
        "orderbook_imbalance": snapshot.orderbook_imbalance,
        "volatility_1h": snapshot.volatility_1h,
        "volatility_24h": snapshot.volatility_24h,
    }


def deserialize_snapshot(payload: dict[str, Any]) -> MarketSnapshot:
    """Rebuild a MarketSnapshot from a Redis stream payload dict.

    Matches the format produced by snapshot_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed MarketSnapshot instance.
    """
    return MarketSnapshot(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        mark_price=Decimal(payload["mark_price"]),
        index_price=Decimal(payload["index_price"]),
        last_price=Decimal(payload["last_price"]),
        best_bid=Decimal(payload["best_bid"]),
        best_ask=Decimal(payload["best_ask"]),
        spread_bps=float(payload["spread_bps"]),
        volume_24h=Decimal(payload["volume_24h"]),
        open_interest=Decimal(payload["open_interest"]),
        funding_rate=Decimal(payload["funding_rate"]),
        next_funding_time=datetime.fromisoformat(payload["next_funding_time"]),
        hours_since_last_funding=float(payload["hours_since_last_funding"]),
        orderbook_imbalance=float(payload["orderbook_imbalance"]),
        volatility_1h=float(payload["volatility_1h"] or 0.0),
        volatility_24h=float(payload["volatility_24h"] or 0.0),
    )


# ---------------------------------------------------------------------------
# StandardSignal
# ---------------------------------------------------------------------------


def signal_to_dict(signal: StandardSignal) -> dict[str, Any]:
    """Serialize a StandardSignal to a JSON-compatible dict for Redis.

    Note: metadata is included directly without numpy conversion.
    Callers in agents that use numpy (e.g. signals agent) must apply
    their own _json_safe() to the metadata value after calling this.

    Args:
        signal: The StandardSignal to serialize.

    Returns:
        Dict ready for Redis publication.
    """
    return {
        "signal_id": signal.signal_id,
        "timestamp": signal.timestamp.isoformat(),
        "instrument": signal.instrument,
        "direction": signal.direction.value,
        "conviction": float(signal.conviction),
        "source": signal.source.value,
        "time_horizon_seconds": int(signal.time_horizon.total_seconds()),
        "reasoning": signal.reasoning,
        "suggested_route": signal.suggested_route.value if signal.suggested_route else None,
        "entry_price": str(signal.entry_price) if signal.entry_price else None,
        "stop_loss": str(signal.stop_loss) if signal.stop_loss else None,
        "take_profit": str(signal.take_profit) if signal.take_profit else None,
        "metadata": signal.metadata,
    }


def deserialize_signal(payload: dict[str, Any]) -> StandardSignal:
    """Rebuild a StandardSignal from a Redis stream payload dict.

    Matches the format produced by signal_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed StandardSignal instance.
    """
    suggested = payload.get("suggested_route")
    return StandardSignal(
        signal_id=payload["signal_id"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        direction=PositionSide(payload["direction"]),
        conviction=float(payload["conviction"]),
        source=SignalSource(payload["source"]),
        time_horizon=timedelta(seconds=float(payload["time_horizon_seconds"])),
        reasoning=payload.get("reasoning", ""),
        suggested_route=Route(suggested) if suggested else None,
        entry_price=Decimal(payload["entry_price"]) if payload.get("entry_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        metadata=payload.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# RankedTradeIdea
# ---------------------------------------------------------------------------


def idea_to_dict(idea: RankedTradeIdea) -> dict[str, Any]:
    """Serialize a RankedTradeIdea for Redis.

    Format must match deserialize_idea().

    Args:
        idea: The RankedTradeIdea to serialize.

    Returns:
        Dict ready for Redis publication.
    """
    return {
        "idea_id": idea.idea_id,
        "timestamp": idea.timestamp.isoformat(),
        "instrument": idea.instrument,
        "route": idea.route.value,
        "direction": idea.direction.value,
        "conviction": idea.conviction,
        "sources": ",".join(s.value for s in idea.sources),
        "time_horizon_seconds": int(idea.time_horizon.total_seconds()),
        "entry_price": str(idea.entry_price) if idea.entry_price else None,
        "stop_loss": str(idea.stop_loss) if idea.stop_loss else None,
        "take_profit": str(idea.take_profit) if idea.take_profit else None,
        "reasoning": idea.reasoning,
    }


def deserialize_idea(payload: dict[str, Any]) -> RankedTradeIdea:
    """Rebuild a RankedTradeIdea from a Redis stream payload dict.

    Matches the format produced by idea_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed RankedTradeIdea instance.
    """
    return RankedTradeIdea(
        idea_id=payload["idea_id"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        route=Route(payload["route"]),
        direction=PositionSide(payload["direction"]),
        conviction=float(payload["conviction"]),
        sources=[SignalSource(s) for s in payload["sources"].split(",")],
        time_horizon=timedelta(seconds=float(payload["time_horizon_seconds"])),
        entry_price=Decimal(payload["entry_price"]) if payload.get("entry_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        reasoning=payload.get("reasoning", ""),
    )


# ---------------------------------------------------------------------------
# ProposedOrder
# ---------------------------------------------------------------------------


def order_to_dict(order: ProposedOrder) -> dict[str, Any]:
    """Serialize a ProposedOrder to a JSON-compatible dict for Redis.

    Args:
        order: The ProposedOrder to serialize.

    Returns:
        Dict ready for Redis publication.
    """
    return {
        "order_id": order.order_id,
        "signal_id": order.signal_id,
        "instrument": order.instrument,
        "route": order.route.value,
        "side": order.side.value,
        "size": str(order.size),
        "order_type": order.order_type.value,
        "conviction": order.conviction,
        "sources": ",".join(s.value for s in order.sources),
        "estimated_margin_required_usdc": str(order.estimated_margin_required_usdc),
        "estimated_liquidation_price": str(order.estimated_liquidation_price),
        "estimated_fee_usdc": str(order.estimated_fee_usdc),
        "estimated_funding_cost_1h_usdc": str(order.estimated_funding_cost_1h_usdc),
        "proposed_at": order.proposed_at.isoformat(),
        "limit_price": str(order.limit_price) if order.limit_price else "",
        "stop_loss": str(order.stop_loss) if order.stop_loss else "",
        "take_profit": str(order.take_profit) if order.take_profit else "",
        "leverage": str(order.leverage),
        "reduce_only": str(order.reduce_only),
        "status": order.status.value,
        "reasoning": order.reasoning,
    }


def deserialize_proposed_order(payload: dict[str, Any]) -> ProposedOrder:
    """Reconstruct a ProposedOrder from a stream:approved_orders:a payload.

    Matches the format produced by order_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed ProposedOrder instance.
    """
    return ProposedOrder(
        order_id=payload["order_id"],
        signal_id=payload["signal_id"],
        instrument=payload["instrument"],
        route=Route(payload["route"]),
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        order_type=OrderType(payload["order_type"]),
        conviction=float(payload["conviction"]),
        sources=[SignalSource(s) for s in payload["sources"].split(",") if s],
        estimated_margin_required_usdc=Decimal(
            payload["estimated_margin_required_usdc"],
        ),
        estimated_liquidation_price=Decimal(payload["estimated_liquidation_price"]),
        estimated_fee_usdc=Decimal(payload["estimated_fee_usdc"]),
        estimated_funding_cost_1h_usdc=Decimal(
            payload["estimated_funding_cost_1h_usdc"],
        ),
        proposed_at=datetime.fromisoformat(payload["proposed_at"]),
        limit_price=Decimal(payload["limit_price"]) if payload.get("limit_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        leverage=Decimal(payload["leverage"]),
        reduce_only=_parse_bool(payload["reduce_only"]),
        status=OrderStatus(payload["status"]),
        reasoning=payload.get("reasoning", ""),
    )


# ---------------------------------------------------------------------------
# ApprovedOrder
# ---------------------------------------------------------------------------


def approved_order_to_dict(order: ApprovedOrder) -> dict[str, Any]:
    """Serialize an ApprovedOrder for publishing to stream:confirmed_orders.

    Args:
        order: The ApprovedOrder to serialize.

    Returns:
        Dict ready for Redis publication.
    """
    return {
        "order_id": order.order_id,
        "route": order.route.value,
        "instrument": order.instrument,
        "side": order.side.value,
        "size": str(order.size),
        "order_type": order.order_type.value,
        "limit_price": str(order.limit_price) if order.limit_price else "",
        "stop_loss": str(order.stop_loss) if order.stop_loss else "",
        "take_profit": str(order.take_profit) if order.take_profit else "",
        "leverage": str(order.leverage),
        "reduce_only": str(order.reduce_only),
        "approved_at": order.approved_at.isoformat(),
    }


def deserialize_approved_order(payload: dict[str, Any]) -> ApprovedOrder:
    """Reconstruct an ApprovedOrder from stream:confirmed_orders payload.

    Matches the format produced by approved_order_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed ApprovedOrder instance.
    """
    return ApprovedOrder(
        order_id=payload["order_id"],
        route=Route(payload["route"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        order_type=OrderType(payload["order_type"]),
        limit_price=Decimal(payload["limit_price"]) if payload.get("limit_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        leverage=Decimal(payload["leverage"]),
        reduce_only=_parse_bool(payload["reduce_only"]),
        approved_at=datetime.fromisoformat(payload["approved_at"]),
    )


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


def fill_to_dict(fill: Fill) -> dict[str, Any]:
    """Serialize a Fill to a dict for publishing to stream:exchange_events:*.

    Args:
        fill: The Fill to serialize.

    Returns:
        Dict ready for Redis publication.
    """
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "route": fill.route.value,
        "instrument": fill.instrument,
        "side": fill.side.value,
        "size": str(fill.size),
        "price": str(fill.price),
        "fee_usdc": str(fill.fee_usdc),
        "is_maker": str(fill.is_maker),
        "filled_at": fill.filled_at.isoformat(),
        "trade_id": fill.trade_id,
    }


def deserialize_fill(payload: dict[str, Any]) -> Fill:
    """Reconstruct a Fill from stream:exchange_events:* payload.

    Matches the format produced by fill_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed Fill instance.
    """
    return Fill(
        fill_id=payload["fill_id"],
        order_id=payload["order_id"],
        route=Route(payload["route"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        price=Decimal(payload["price"]),
        fee_usdc=Decimal(payload["fee_usdc"]),
        is_maker=_parse_bool(payload["is_maker"]),
        filled_at=datetime.fromisoformat(payload["filled_at"]),
        trade_id=payload["trade_id"],
    )


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------


def portfolio_snapshot_to_dict(snap: PortfolioSnapshot) -> dict[str, Any]:
    """Serialize a PortfolioSnapshot for stream:portfolio_state:*.

    Args:
        snap: The PortfolioSnapshot to serialize.

    Returns:
        Dict ready for Redis publication.
    """
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
    """Reconstruct a PortfolioSnapshot from stream:portfolio_state payload.

    Positions are not serialized in the stream; the deserialized snapshot
    always has an empty positions list.

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed PortfolioSnapshot instance with empty positions.
    """
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


# ---------------------------------------------------------------------------
# FundingPayment
# ---------------------------------------------------------------------------


def funding_payment_to_dict(payment: FundingPayment) -> dict[str, Any]:
    """Serialize a FundingPayment for stream:funding_payments:*.

    Args:
        payment: The FundingPayment to serialize.

    Returns:
        Dict ready for Redis publication.
    """
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
    """Reconstruct a FundingPayment from stream:funding_payments payload.

    Matches the format produced by funding_payment_to_dict().

    Args:
        payload: Dict from a Redis stream message.

    Returns:
        Reconstructed FundingPayment instance.
    """
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
