"""Compose Telegram messages for trade confirmations.

Pure text formatting — no Telegram SDK dependency.  The bot layer
calls these helpers and sends the resulting strings.
"""

from __future__ import annotations

from decimal import Decimal

from libs.common.models.order import ProposedOrder


def compose_trade_request(
    order: ProposedOrder,
    *,
    portfolio_equity_usdc: Decimal | None = None,
    margin_utilization_pct: float | None = None,
    sequence_number: int | None = None,
) -> str:
    """Format a ProposedOrder into a rich Telegram confirmation message.

    Matches the format from CLAUDE.md:
        Trade Request #NNNN [Route B]
        instrument — SIDE
        size, entry, stop, TP, leverage
        signal sources + reasoning
        risk summary (margin, liquidation, fees, funding)
    """
    seq = f"#{sequence_number:04d}" if sequence_number else f"#{_short_id(order.order_id)}"
    direction = "LONG" if order.side.value == "BUY" else "SHORT"
    notional = order.notional_usdc

    fee_label = "maker" if order.order_type.value == "LIMIT" else "taker"

    lines = [
        f"Trade Request {seq} [Route B]",
        "",
        f"{order.instrument} -- {direction}",
        f"Size: {order.size} ETH (~{notional:,.2f} USDC)",
    ]

    if order.limit_price is not None:
        lines.append(f"Entry: ~${order.limit_price:,.2f} ({order.order_type.value.lower()}, {fee_label})")
    else:
        lines.append(f"Entry: market ({fee_label})")

    if order.stop_loss is not None:
        sl_pct = _pct_from_entry(order.limit_price, order.stop_loss)
        lines.append(f"Stop-loss: ${order.stop_loss:,.2f} ({sl_pct})")
    if order.take_profit is not None:
        tp_pct = _pct_from_entry(order.limit_price, order.take_profit)
        lines.append(f"Take-profit: ${order.take_profit:,.2f} ({tp_pct})")

    lines.append(f"Leverage: {order.leverage}x")

    # Signal info
    sources_str = ", ".join(s.value.replace("_", " ").title() for s in order.sources)
    conv_str = f"{order.conviction:.2f}"
    lines.append("")
    lines.append(f"Signal: {sources_str} ({conv_str})")
    if order.reasoning:
        lines.append(f"Catalyst: {order.reasoning}")

    # Risk summary
    lines.append("")
    lines.append("Risk Summary:")
    lines.append(f"  Margin required: {order.estimated_margin_required_usdc:,.2f} USDC")

    liq_distance = _liq_distance_pct(order.limit_price, order.estimated_liquidation_price)
    lines.append(f"  Liquidation price: ${order.estimated_liquidation_price:,.2f} ({liq_distance} away)")
    lines.append(f"  Est. fees: {order.estimated_fee_usdc:,.2f} USDC ({fee_label})")

    funding_sign = "you receive" if order.estimated_funding_cost_1h_usdc < 0 else "cost"
    lines.append(
        f"  Funding cost (est. next hour): "
        f"{order.estimated_funding_cost_1h_usdc:,.2f} USDC ({funding_sign})",
    )

    if portfolio_equity_usdc is not None:
        margin_str = f" | Margin: {margin_utilization_pct:.0f}%" if margin_utilization_pct is not None else ""
        lines.append(f"  Route B equity: {portfolio_equity_usdc:,.2f} USDC{margin_str}")

    return "\n".join(lines)


def compose_batch_header(count: int) -> str:
    """Header line for a batched group of orders."""
    return f"Batch of {count} trade requests [Route B]\n"


def compose_expiry_notice(order: ProposedOrder) -> str:
    """Notification that an order expired without user action."""
    direction = "LONG" if order.side.value == "BUY" else "SHORT"
    return (
        f"Expired: {order.instrument} {direction} "
        f"{order.size} ETH @ ${order.limit_price or 'market'} "
        f"(conviction {order.conviction:.2f}) — no response within TTL"
    )


def compose_stale_price_warning(
    order: ProposedOrder,
    proposed_price: Decimal,
    current_price: Decimal,
) -> str:
    """Warning that the mark price has moved significantly since proposal."""
    pct = abs(float(current_price - proposed_price)) / float(proposed_price) * 100
    return (
        f"Price moved {pct:.1f}% since proposal for order {_short_id(order.order_id)}: "
        f"${proposed_price:,.2f} -> ${current_price:,.2f}. "
        f"Consider rejecting and re-evaluating."
    )


# -- Helpers -------------------------------------------------------------------


def _short_id(order_id: str) -> str:
    """First 8 chars of the order ID for display."""
    return order_id[:8]


def _pct_from_entry(entry: Decimal | None, target: Decimal) -> str:
    """Format percentage distance from entry to target."""
    if entry is None or entry == 0:
        return ""
    pct = (float(target) - float(entry)) / float(entry) * 100
    return f"{pct:+.2f}%"


def _liq_distance_pct(entry: Decimal | None, liq_price: Decimal) -> str:
    """Format liquidation distance percentage."""
    if entry is None or entry == 0:
        return "N/A"
    pct = abs(float(liq_price) - float(entry)) / float(entry) * 100
    return f"{pct:.1f}%"
