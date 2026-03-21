"""Paper trading simulator for the reconciliation agent.

In paper mode (ENVIRONMENT=paper), this module replaces the Coinbase API
polling loop. It simulates order execution and portfolio state by:

  1. Consuming approved orders from stream:approved_orders:a (and :b if configured)
  2. Simulating instant fills at the current mark price
  3. Tracking simulated positions, equity, margin, funding, and fees
  4. Publishing PortfolioSnapshots to stream:portfolio_state:a/b
  5. Publishing simulated Fills to stream:exchange_events:a/b
  6. Applying hourly funding payments based on real funding rates from ingestion

Subscribes to:
  - stream:market_snapshots     (latest mark price + funding rate from ingestion)
  - stream:approved_orders:a    (risk-approved orders for Portfolio A)
  - stream:approved_orders:b    (risk-approved orders for Portfolio B, bypasses confirmation)

Publishes to:
  - stream:exchange_events:a/b  (simulated fills)
  - stream:portfolio_state:a/b  (simulated portfolio snapshots)
  - stream:funding_payments:a/b (simulated hourly funding)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    PositionSide,
    SignalSource,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill, ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.utils import generate_id, utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.reconciliation.main import (
    funding_payment_to_dict,
    portfolio_snapshot_to_dict,
)

from typing import Any

logger = setup_logging("paper_simulator", json_output=False)

# Default initial equity per portfolio in paper mode
PAPER_INITIAL_EQUITY = Decimal("10000")

# How often to publish periodic snapshots (seconds)
SNAPSHOT_INTERVAL = 10


# ---------------------------------------------------------------------------
# Serialization helpers (inlined to avoid cross-agent import dependency)
# ---------------------------------------------------------------------------


def deserialize_proposed_order(payload: dict[str, Any]) -> ProposedOrder:
    """Reconstruct a ProposedOrder from stream:approved_orders payload."""
    return ProposedOrder(
        order_id=payload["order_id"],
        signal_id=payload["signal_id"],
        instrument=payload["instrument"],
        portfolio_target=PortfolioTarget(payload["portfolio_target"]),
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        order_type=OrderType(payload["order_type"]),
        conviction=float(payload["conviction"]),
        sources=[SignalSource(s) for s in payload["sources"].split(",") if s],
        estimated_margin_required_usdc=Decimal(payload["estimated_margin_required_usdc"]),
        estimated_liquidation_price=Decimal(payload["estimated_liquidation_price"]),
        estimated_fee_usdc=Decimal(payload["estimated_fee_usdc"]),
        estimated_funding_cost_1h_usdc=Decimal(payload["estimated_funding_cost_1h_usdc"]),
        proposed_at=datetime.fromisoformat(payload["proposed_at"]),
        limit_price=Decimal(payload["limit_price"]) if payload.get("limit_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        leverage=Decimal(payload["leverage"]),
        reduce_only=payload["reduce_only"] == "True" if isinstance(payload["reduce_only"], str) else bool(payload["reduce_only"]),
        status=OrderStatus(payload["status"]),
        reasoning=payload.get("reasoning", ""),
    )


def fill_to_dict(fill: Fill) -> dict[str, Any]:
    """Serialize a Fill for publishing to stream:exchange_events:*."""
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "portfolio_target": fill.portfolio_target.value,
        "instrument": fill.instrument,
        "side": fill.side.value,
        "size": str(fill.size),
        "price": str(fill.price),
        "fee_usdc": str(fill.fee_usdc),
        "is_maker": str(fill.is_maker),
        "filled_at": fill.filled_at.isoformat(),
        "trade_id": fill.trade_id,
    }


# ---------------------------------------------------------------------------
# Simulated position & portfolio
# ---------------------------------------------------------------------------


@dataclass
class SimulatedPosition:
    """A simulated perpetual futures position."""

    instrument: str
    side: PositionSide
    size: Decimal  # Always positive
    entry_price: Decimal
    cumulative_funding_usdc: Decimal = Decimal("0")
    total_fees_usdc: Decimal = Decimal("0")

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if self.side == PositionSide.LONG:
            return (mark_price - self.entry_price) * self.size
        elif self.side == PositionSide.SHORT:
            return (self.entry_price - mark_price) * self.size
        return Decimal("0")


class PaperPortfolio:
    """Simulated portfolio that tracks positions, equity, and P&L."""

    def __init__(
        self,
        target: PortfolioTarget,
        initial_equity: Decimal,
    ) -> None:
        self.target = target
        self.initial_equity = initial_equity
        self.realized_pnl = Decimal("0")
        self.fees_paid = Decimal("0")
        self.funding_pnl = Decimal("0")
        self.position: SimulatedPosition | None = None
        self.fill_count = 0

    @property
    def base_equity(self) -> Decimal:
        """Equity excluding unrealized P&L."""
        return self.initial_equity + self.realized_pnl - self.fees_paid + self.funding_pnl

    def apply_fill(
        self,
        order_id: str,
        instrument: str,
        side: OrderSide,
        size: Decimal,
        fill_price: Decimal,
        is_maker: bool,
    ) -> Fill:
        """Simulate a fill and update position state."""
        fee_rate = FEE_MAKER if is_maker else FEE_TAKER
        notional = size * fill_price
        fee = (notional * fee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.fees_paid += fee
        self.fill_count += 1

        fill_side = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT

        if self.position is None:
            # Open new position
            self.position = SimulatedPosition(
                instrument=instrument,
                side=fill_side,
                size=size,
                entry_price=fill_price,
                total_fees_usdc=fee,
            )
        elif self.position.side == fill_side:
            # Add to existing position — weighted average entry
            total_size = self.position.size + size
            avg_entry = (
                self.position.entry_price * self.position.size + fill_price * size
            ) / total_size
            self.position.size = total_size
            self.position.entry_price = avg_entry
            self.position.total_fees_usdc += fee
        else:
            # Reducing or closing/flipping
            if size >= self.position.size:
                # Close the full position
                close_pnl = self.position.unrealized_pnl(fill_price)
                self.realized_pnl += close_pnl
                remaining = size - self.position.size
                if remaining > 0:
                    # Flip: open opposite position with the remainder
                    self.position = SimulatedPosition(
                        instrument=instrument,
                        side=fill_side,
                        size=remaining,
                        entry_price=fill_price,
                        total_fees_usdc=fee,
                    )
                else:
                    self.position = None
            else:
                # Partial close
                pnl_per_unit = (
                    (fill_price - self.position.entry_price)
                    if self.position.side == PositionSide.LONG
                    else (self.position.entry_price - fill_price)
                )
                self.realized_pnl += pnl_per_unit * size
                self.position.size -= size
                self.position.total_fees_usdc += fee

        return Fill(
            fill_id=generate_id("fill"),
            order_id=order_id,
            portfolio_target=self.target,
            instrument=instrument,
            side=side,
            size=size,
            price=fill_price,
            fee_usdc=fee,
            is_maker=is_maker,
            filled_at=utc_now(),
            trade_id=generate_id("trade"),
        )

    def apply_funding(self, rate: Decimal, mark_price: Decimal) -> FundingPayment | None:
        """Apply an hourly funding payment. Returns None if no position."""
        if self.position is None or self.position.size == 0:
            return None

        # payment = rate * notional
        # Positive rate: longs pay shorts
        notional = self.position.size * mark_price
        raw_payment = rate * notional

        if self.position.side == PositionSide.LONG:
            payment = -raw_payment  # Longs pay when rate > 0
        else:
            payment = raw_payment  # Shorts receive when rate > 0

        payment = payment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.funding_pnl += payment
        self.position.cumulative_funding_usdc += payment

        return FundingPayment(
            timestamp=utc_now(),
            instrument=self.position.instrument,
            portfolio_target=self.target,
            rate=rate,
            payment_usdc=payment,
            position_size=self.position.size,
            position_side=self.position.side,
            cumulative_24h_usdc=self.funding_pnl,
        )

    def build_snapshot(self, mark_price: Decimal) -> PortfolioSnapshot:
        """Build a PortfolioSnapshot at the current mark price."""
        positions: list[PerpPosition] = []
        unrealized_pnl = Decimal("0")
        used_margin = Decimal("0")

        if self.position and self.position.size > 0:
            unrealized = self.position.unrealized_pnl(mark_price)
            unrealized_pnl = unrealized
            notional = self.position.size * mark_price
            leverage = Decimal("3")
            initial_margin = (notional / leverage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            maint_margin = (initial_margin / 2).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            used_margin = initial_margin

            # Estimate liquidation price
            if self.position.side == PositionSide.LONG:
                liq_price = self.position.entry_price * (1 - Decimal("1") / leverage)
            else:
                liq_price = self.position.entry_price * (1 + Decimal("1") / leverage)
            liq_price = liq_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            positions.append(PerpPosition(
                instrument=self.position.instrument,
                portfolio_target=self.target,
                side=self.position.side,
                size=self.position.size,
                entry_price=self.position.entry_price,
                mark_price=mark_price,
                unrealized_pnl_usdc=unrealized,
                realized_pnl_usdc=self.realized_pnl,
                leverage=leverage,
                initial_margin_usdc=initial_margin,
                maintenance_margin_usdc=maint_margin,
                liquidation_price=liq_price,
                margin_ratio=float(maint_margin / initial_margin) if initial_margin > 0 else 0.0,
                cumulative_funding_usdc=self.position.cumulative_funding_usdc,
                total_fees_usdc=self.position.total_fees_usdc,
            ))

        equity = self.base_equity + unrealized_pnl
        available_margin = max(equity - used_margin, Decimal("0"))
        margin_util = float(used_margin / equity * 100) if equity > 0 else 0.0

        return PortfolioSnapshot(
            timestamp=utc_now(),
            portfolio_target=self.target,
            equity_usdc=equity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            used_margin_usdc=used_margin,
            available_margin_usdc=available_margin.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
            margin_utilization_pct=round(margin_util, 1),
            positions=positions,
            unrealized_pnl_usdc=unrealized_pnl.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
            realized_pnl_today_usdc=self.realized_pnl.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
            funding_pnl_today_usdc=self.funding_pnl.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
            fees_paid_today_usdc=self.fees_paid.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
        )


# ---------------------------------------------------------------------------
# Main simulator loop
# ---------------------------------------------------------------------------


async def run_paper_simulator(
    redis_url: str,
    publisher: RedisPublisher,
    *,
    include_portfolio_b: bool = True,
) -> None:
    """Run the paper trading simulator.

    Creates simulated portfolios, consumes approved orders, simulates fills,
    and publishes portfolio state — all without touching the Coinbase API.
    """
    portfolios: dict[PortfolioTarget, PaperPortfolio] = {
        PortfolioTarget.A: PaperPortfolio(
            target=PortfolioTarget.A,
            initial_equity=PAPER_INITIAL_EQUITY,
        ),
    }

    if include_portfolio_b:
        portfolios[PortfolioTarget.B] = PaperPortfolio(
            target=PortfolioTarget.B,
            initial_equity=PAPER_INITIAL_EQUITY,
        )

    # Shared market data cache
    latest_mark_price: Decimal | None = None
    latest_funding_rate = Decimal("0")

    # --- Consumer: market data ---
    market_consumer = RedisConsumer(redis_url=redis_url, block_ms=2000)
    await market_consumer.subscribe(
        [Channel.MARKET_SNAPSHOTS],
        group="paper_sim_market",
        consumer_name="paper_sim_market_0",
    )

    # --- Consumer: approved orders ---
    order_channels = [Channel.approved_orders(PortfolioTarget.A)]
    if include_portfolio_b:
        order_channels.append(Channel.approved_orders(PortfolioTarget.B))

    order_consumer = RedisConsumer(redis_url=redis_url, block_ms=2000)
    await order_consumer.subscribe(
        order_channels,
        group="paper_sim_exec",
        consumer_name="paper_sim_exec_0",
    )

    # --- Task: cache latest market data ---
    async def market_data_reader() -> None:
        nonlocal latest_mark_price, latest_funding_rate
        async for channel, msg_id, payload in market_consumer.listen():
            try:
                latest_mark_price = Decimal(payload["mark_price"])
                fr = payload.get("funding_rate")
                if fr is not None:
                    latest_funding_rate = Decimal(str(fr))
            except Exception:
                pass
            await market_consumer.ack(channel, "paper_sim_market", msg_id)

    # --- Task: process orders and simulate fills ---
    async def order_processor() -> None:
        async for channel, msg_id, payload in order_consumer.listen():
            if latest_mark_price is None:
                logger.warning("paper_no_market_data", msg="skipping order, waiting for market data")
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            try:
                order = deserialize_proposed_order(payload)
            except Exception as e:
                logger.error("paper_deserialize_error", error=str(e))
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            portfolio = portfolios.get(order.portfolio_target)
            if portfolio is None:
                logger.warning("paper_no_portfolio", target=order.portfolio_target.value)
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            # Fill at limit price (if set and order is LIMIT), otherwise mark price
            is_maker = order.order_type == OrderType.LIMIT
            fill_price = (
                order.limit_price
                if is_maker and order.limit_price
                else latest_mark_price
            )

            fill = portfolio.apply_fill(
                order_id=order.order_id,
                instrument=order.instrument,
                side=order.side,
                size=order.size,
                fill_price=fill_price,
                is_maker=is_maker,
            )

            # Publish fill
            events_ch = Channel.exchange_events(portfolio.target)
            await publisher.publish(events_ch, fill_to_dict(fill))

            # Publish updated snapshot
            snapshot = portfolio.build_snapshot(latest_mark_price)
            state_ch = Channel.portfolio_state(portfolio.target)
            await publisher.publish(state_ch, portfolio_snapshot_to_dict(snapshot))

            logger.info(
                "paper_fill",
                portfolio=portfolio.target.value,
                side=order.side.value,
                size=str(fill.size),
                price=str(fill.price),
                fee=str(fill.fee_usdc),
                equity=str(snapshot.equity_usdc),
                positions=len(snapshot.open_positions),
            )

            await order_consumer.ack(channel, "paper_sim_exec", msg_id)

    # --- Task: publish periodic snapshots ---
    async def periodic_snapshots() -> None:
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            if latest_mark_price is None:
                continue
            for portfolio in portfolios.values():
                snapshot = portfolio.build_snapshot(latest_mark_price)
                ch = Channel.portfolio_state(portfolio.target)
                await publisher.publish(ch, portfolio_snapshot_to_dict(snapshot))
                logger.debug(
                    "paper_snapshot",
                    portfolio=portfolio.target.value,
                    equity=str(snapshot.equity_usdc),
                    unrealized_pnl=str(snapshot.unrealized_pnl_usdc),
                    positions=len(snapshot.open_positions),
                )

    # --- Task: apply hourly funding ---
    async def funding_applier() -> None:
        while True:
            now = utc_now()
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            wait = (next_hour - now).total_seconds()
            await asyncio.sleep(wait)

            if latest_mark_price is None:
                continue

            for portfolio in portfolios.values():
                payment = portfolio.apply_funding(latest_funding_rate, latest_mark_price)
                if payment is not None:
                    ch = Channel.funding_payments(portfolio.target)
                    await publisher.publish(ch, funding_payment_to_dict(payment))
                    logger.info(
                        "paper_funding",
                        portfolio=portfolio.target.value,
                        rate=str(latest_funding_rate),
                        payment=str(payment.payment_usdc),
                        cumulative=str(payment.cumulative_24h_usdc),
                    )

    # --- Start all tasks ---
    portfolio_labels = ", ".join(
        f"{t.value} (${p.initial_equity})"
        for t, p in portfolios.items()
    )
    logger.info("paper_simulator_started", portfolios=portfolio_labels)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(market_data_reader())
            tg.create_task(order_processor())
            tg.create_task(periodic_snapshots())
            tg.create_task(funding_applier())
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("paper_simulator_error", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await market_consumer.close()
        await order_consumer.close()
        logger.info("paper_simulator_stopped")
