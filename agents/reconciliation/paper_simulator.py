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
  - stream:approved_orders:a    (risk-approved orders for Route A)
  - stream:approved_orders:b    (risk-approved orders for Route B, bypasses confirmation)

Publishes to:
  - stream:exchange_events:a/b  (simulated fills)
  - stream:portfolio_state:a/b  (simulated portfolio snapshots)
  - stream:funding_payments:a/b (simulated hourly funding)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import SQLAlchemyError

from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    Route,
    PositionSide,
    SignalSource,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill, ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.serialization import (
    deserialize_proposed_order,
    fill_to_dict,
    funding_payment_to_dict,
    portfolio_snapshot_to_dict,
)
from libs.common.utils import generate_id, utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from libs.storage.models import FillRecord
from libs.storage.repository import TunerRepository

logger = setup_logging("paper_simulator", json_output=False)


# ---------------------------------------------------------------------------
# Probabilistic fill model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperSimulatorConfig:
    """Configuration for the paper simulator's probabilistic fill model.

    Defaults reproduce pre-fidelity behavior: all limit orders fill at the
    exact limit price with no adverse selection.

    Args:
        fill_probability_base: Base fill probability for a limit order at mark
            price. Range [0, 1]. Orders far from mark are discounted by a
            proximity factor. Default 0.7 (matches typical liquidity).
        adverse_selection_bps: Basis points of adverse price movement applied
            to filled limit orders. BUY fills pay more; SELL fills receive
            less. Range [0, 50]. Default 5 bps.
        sl_slippage_bps: Basis points of slippage applied to stop-loss fills.
            Range [0, 50]. Default 10 bps.
    """

    fill_probability_base: Decimal = Decimal("0.7")
    adverse_selection_bps: Decimal = Decimal("5")
    sl_slippage_bps: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.fill_probability_base <= Decimal("1")):
            raise ValueError(
                f"fill_probability_base must be in [0, 1], got {self.fill_probability_base}"
            )
        if self.adverse_selection_bps < Decimal("0") or self.adverse_selection_bps > Decimal("50"):
            raise ValueError(
                f"adverse_selection_bps must be in [0, 50], got {self.adverse_selection_bps}"
            )
        if self.sl_slippage_bps < Decimal("0") or self.sl_slippage_bps > Decimal("50"):
            raise ValueError(
                f"sl_slippage_bps must be in [0, 50], got {self.sl_slippage_bps}"
            )


def _decide_fill(
    side: OrderSide,
    order_type: OrderType,
    limit_price: Decimal | None,
    mark_price: Decimal,
    cfg: PaperSimulatorConfig,
    rng: random.Random,
) -> tuple[bool, Decimal]:
    """Decide whether an order fills and at what effective price.

    For MARKET orders (or when limit_price is None): always fills at mark price
    with no adverse selection.

    For LIMIT orders: fill probability is scaled by proximity to mark price.
    Filled orders experience adverse selection (BUY pays more, SELL less).

    Args:
        side: Order side (BUY or SELL).
        order_type: MARKET or LIMIT.
        limit_price: Limit price for LIMIT orders; None for MARKET orders.
        mark_price: Current mark price for the instrument.
        cfg: Simulator configuration.
        rng: Seeded random instance for deterministic tests.

    Returns:
        Tuple of (should_fill, effective_price). If should_fill is False,
        effective_price is Decimal("0").
    """
    # MARKET or no limit_price: always fill at mark, no adverse selection
    if order_type == OrderType.MARKET or limit_price is None:
        return (True, mark_price)

    # Guard against division by zero
    if mark_price == Decimal("0"):
        return (False, Decimal("0"))

    # Compute fill probability scaled by price proximity
    distance_bps = abs(limit_price - mark_price) / mark_price * Decimal("10000")
    proximity_factor = Decimal("1") / (Decimal("1") + distance_bps / Decimal("100"))
    probability = float(cfg.fill_probability_base * proximity_factor)

    if rng.random() > probability:
        return (False, Decimal("0"))

    # Apply adverse selection to filled price
    bps_factor = cfg.adverse_selection_bps / Decimal("10000")
    if side == OrderSide.BUY:
        effective_price = limit_price * (Decimal("1") + bps_factor)
    else:
        effective_price = limit_price * (Decimal("1") - bps_factor)

    effective_price = effective_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (True, effective_price)


# Default initial equity per portfolio in paper mode
PAPER_INITIAL_EQUITY = Decimal("10000")

# How often to publish periodic snapshots (seconds)
SNAPSHOT_INTERVAL = 10


async def _persist_fill(repo: TunerRepository | None, fill: Fill) -> None:
    """Persist a fill to PostgreSQL if a repository is available.

    Logs a warning on failure but never raises — fill persistence is
    best-effort and must not block the simulator's main loop.
    """
    if repo is None:
        return
    try:
        await repo.write_fill(FillRecord(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            portfolio_target=fill.route.value,
            instrument=fill.instrument,
            side=fill.side.value,
            size=fill.size,
            price=fill.price,
            fee_usdc=fill.fee_usdc,
            is_maker=fill.is_maker,
            filled_at=fill.filled_at,
            trade_id=fill.trade_id,
        ))
    except SQLAlchemyError as exc:
        logger.warning(
            "paper_fill_db_write_failed",
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
    except Exception as exc:
        logger.warning(
            "paper_fill_db_write_failed",
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )


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
    """Simulated portfolio that tracks per-instrument positions, equity, and P&L."""

    def __init__(
        self,
        target: Route,
        initial_equity: Decimal,
    ) -> None:
        self.target = target
        self.initial_equity = initial_equity
        self.realized_pnl = Decimal("0")
        self.realized_pnl_per_instrument: dict[str, Decimal] = {}
        self.fees_paid = Decimal("0")
        self.funding_pnl = Decimal("0")
        self.positions: dict[str, SimulatedPosition] = {}
        self.fill_count = 0

    @classmethod
    def restore_from_fills(
        cls,
        target: Route,
        fill_records: list[Any],
    ) -> "PaperPortfolio":
        """Reconstruct a PaperPortfolio by replaying persisted fill history.

        Replays fills in chronological order (oldest first) to rebuild
        realized_pnl, fees_paid, fill_count, and open positions.
        funding_pnl is not recoverable from fills alone and stays at 0.

        Args:
            target: Route this portfolio belongs to.
            fill_records: FillRecord ORM rows ordered by filled_at ascending.

        Returns:
            PaperPortfolio with state matching the end of the fill history.
        """
        portfolio = cls(target=target, initial_equity=PAPER_INITIAL_EQUITY)
        for rec in fill_records:
            side = OrderSide(rec.side)
            fee_rate = FEE_MAKER if rec.is_maker else FEE_TAKER
            fee = rec.fee_usdc  # Already computed — use stored value directly
            portfolio.fees_paid += fee
            portfolio.fill_count += 1

            fill_side = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
            instrument = rec.instrument
            size = rec.size
            fill_price = rec.price

            pos = portfolio.positions.get(instrument)

            if pos is None or pos.size == 0:
                portfolio.positions[instrument] = SimulatedPosition(
                    instrument=instrument,
                    side=fill_side,
                    size=size,
                    entry_price=fill_price,
                    total_fees_usdc=fee,
                )
            elif pos.side == fill_side:
                total_size = pos.size + size
                avg_entry = (pos.entry_price * pos.size + fill_price * size) / total_size
                pos.size = total_size
                pos.entry_price = avg_entry
                pos.total_fees_usdc += fee
            else:
                if size >= pos.size:
                    close_pnl = pos.unrealized_pnl(fill_price)
                    portfolio.realized_pnl += close_pnl
                    portfolio.realized_pnl_per_instrument[instrument] = (
                        portfolio.realized_pnl_per_instrument.get(instrument, Decimal("0"))
                        + close_pnl
                    )
                    remaining = size - pos.size
                    if remaining > 0:
                        portfolio.positions[instrument] = SimulatedPosition(
                            instrument=instrument,
                            side=fill_side,
                            size=remaining,
                            entry_price=fill_price,
                            total_fees_usdc=fee,
                        )
                    else:
                        del portfolio.positions[instrument]
                else:
                    pnl_per_unit = (
                        (fill_price - pos.entry_price)
                        if pos.side == PositionSide.LONG
                        else (pos.entry_price - fill_price)
                    )
                    partial_pnl = pnl_per_unit * size
                    portfolio.realized_pnl += partial_pnl
                    portfolio.realized_pnl_per_instrument[instrument] = (
                        portfolio.realized_pnl_per_instrument.get(instrument, Decimal("0"))
                        + partial_pnl
                    )
                    pos.size -= size
                    pos.total_fees_usdc += fee

        return portfolio

    @property
    def position(self) -> SimulatedPosition | None:
        """Legacy accessor — returns first open position or None.

        Kept for backward compatibility with tests that check portfolio.position.
        """
        for pos in self.positions.values():
            if pos.size > 0:
                return pos
        return None

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
        """Simulate a fill and update the instrument's position state."""
        fee_rate = FEE_MAKER if is_maker else FEE_TAKER
        notional = size * fill_price
        fee = (notional * fee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.fees_paid += fee
        self.fill_count += 1

        fill_side = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT

        pos = self.positions.get(instrument)

        if pos is None or pos.size == 0:
            # Open new position for this instrument
            self.positions[instrument] = SimulatedPosition(
                instrument=instrument,
                side=fill_side,
                size=size,
                entry_price=fill_price,
                total_fees_usdc=fee,
            )
        elif pos.side == fill_side:
            # Add to existing position — weighted average entry
            total_size = pos.size + size
            avg_entry = (pos.entry_price * pos.size + fill_price * size) / total_size
            pos.size = total_size
            pos.entry_price = avg_entry
            pos.total_fees_usdc += fee
        else:
            # Reducing or closing/flipping
            if size >= pos.size:
                # Close the full position
                close_pnl = pos.unrealized_pnl(fill_price)
                self.realized_pnl += close_pnl
                self.realized_pnl_per_instrument[instrument] = (
                    self.realized_pnl_per_instrument.get(instrument, Decimal("0"))
                    + close_pnl
                )
                remaining = size - pos.size
                if remaining > 0:
                    # Flip: open opposite position with the remainder
                    self.positions[instrument] = SimulatedPosition(
                        instrument=instrument,
                        side=fill_side,
                        size=remaining,
                        entry_price=fill_price,
                        total_fees_usdc=fee,
                    )
                else:
                    del self.positions[instrument]
            else:
                # Partial close
                pnl_per_unit = (
                    (fill_price - pos.entry_price)
                    if pos.side == PositionSide.LONG
                    else (pos.entry_price - fill_price)
                )
                partial_pnl = pnl_per_unit * size
                self.realized_pnl += partial_pnl
                self.realized_pnl_per_instrument[instrument] = (
                    self.realized_pnl_per_instrument.get(instrument, Decimal("0"))
                    + partial_pnl
                )
                pos.size -= size
                pos.total_fees_usdc += fee

        return Fill(
            fill_id=generate_id("fill"),
            order_id=order_id,
            route=self.target,
            instrument=instrument,
            side=side,
            size=size,
            price=fill_price,
            fee_usdc=fee,
            is_maker=is_maker,
            filled_at=utc_now(),
            trade_id=generate_id("trade"),
        )

    def apply_funding(
        self,
        instrument: str,
        rate: Decimal,
        mark_price: Decimal,
    ) -> FundingPayment | None:
        """Apply an hourly funding payment for a specific instrument."""
        pos = self.positions.get(instrument)
        if pos is None or pos.size == 0:
            return None

        notional = pos.size * mark_price
        raw_payment = rate * notional

        if pos.side == PositionSide.LONG:
            payment = -raw_payment  # Longs pay when rate > 0
        else:
            payment = raw_payment  # Shorts receive when rate > 0

        payment = payment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.funding_pnl += payment
        pos.cumulative_funding_usdc += payment

        return FundingPayment(
            timestamp=utc_now(),
            instrument=instrument,
            route=self.target,
            rate=rate,
            payment_usdc=payment,
            position_size=pos.size,
            position_side=pos.side,
            cumulative_24h_usdc=self.funding_pnl,
        )

    def build_snapshot(
        self,
        mark_prices: dict[str, Decimal],
    ) -> PortfolioSnapshot:
        """Build a PortfolioSnapshot using per-instrument mark prices."""
        perp_positions: list[PerpPosition] = []
        total_unrealized = Decimal("0")
        total_used_margin = Decimal("0")

        for instrument, pos in self.positions.items():
            if pos.size == 0:
                continue
            mark_price = mark_prices.get(instrument, Decimal("0"))
            if mark_price == 0:
                mark_price = pos.entry_price
            unrealized = pos.unrealized_pnl(mark_price)
            total_unrealized += unrealized
            notional = pos.size * mark_price
            leverage = Decimal("3")
            initial_margin = (notional / leverage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            maint_margin = (initial_margin / 2).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            total_used_margin += initial_margin

            if pos.side == PositionSide.LONG:
                liq_price = pos.entry_price * (1 - Decimal("1") / leverage)
            else:
                liq_price = pos.entry_price * (1 + Decimal("1") / leverage)
            liq_price = liq_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            perp_positions.append(PerpPosition(
                instrument=instrument,
                route=self.target,
                side=pos.side,
                size=pos.size,
                entry_price=pos.entry_price,
                mark_price=mark_price,
                unrealized_pnl_usdc=unrealized,
                realized_pnl_usdc=self.realized_pnl_per_instrument.get(instrument, Decimal("0")),
                leverage=leverage,
                initial_margin_usdc=initial_margin,
                maintenance_margin_usdc=maint_margin,
                liquidation_price=liq_price,
                margin_ratio=float(maint_margin / initial_margin) if initial_margin > 0 else 0.0,
                cumulative_funding_usdc=pos.cumulative_funding_usdc,
                total_fees_usdc=pos.total_fees_usdc,
            ))

        equity = self.base_equity + total_unrealized
        available_margin = max(equity - total_used_margin, Decimal("0"))
        margin_util = float(total_used_margin / equity * 100) if equity > 0 else 0.0

        return PortfolioSnapshot(
            timestamp=utc_now(),
            route=self.target,
            equity_usdc=equity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            used_margin_usdc=total_used_margin,
            available_margin_usdc=available_margin.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
            margin_utilization_pct=round(margin_util, 1),
            positions=perp_positions,
            unrealized_pnl_usdc=total_unrealized.quantize(
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
# Pending protective orders (SL/TP)
# ---------------------------------------------------------------------------


@dataclass
class PendingProtectiveOrder:
    """A stop-loss or take-profit order waiting to be triggered."""

    order_id: str
    instrument: str
    route: Route
    side: OrderSide  # Close side (opposite of position)
    size: Decimal
    trigger_price: Decimal  # Price at which the order activates
    fill_price: Decimal  # Price to fill at (limit price)
    is_stop_loss: bool  # True = SL, False = TP

    def is_triggered(self, mark_price: Decimal) -> bool:
        """Check if the current mark price triggers this order.

        Stop-loss (closing a LONG): triggers when mark <= trigger
        Stop-loss (closing a SHORT): triggers when mark >= trigger
        Take-profit (closing a LONG): triggers when mark >= trigger
        Take-profit (closing a SHORT): triggers when mark <= trigger
        """
        if self.is_stop_loss:
            # SL closes position — BUY side means we're closing a SHORT (trigger on rise)
            if self.side == OrderSide.BUY:
                return mark_price >= self.trigger_price
            else:
                return mark_price <= self.trigger_price
        else:
            # TP closes position — BUY side means we're closing a SHORT (trigger on drop)
            if self.side == OrderSide.BUY:
                return mark_price <= self.trigger_price
            else:
                return mark_price >= self.trigger_price


# ---------------------------------------------------------------------------
# Main simulator loop
# ---------------------------------------------------------------------------


async def run_paper_simulator(
    redis_url: str,
    publisher: RedisPublisher,
    *,
    include_route_b: bool = True,
    repo: TunerRepository | None = None,
) -> None:
    """Run the paper trading simulator.

    Creates simulated portfolios, consumes approved orders, simulates fills,
    and publishes portfolio state — all without touching the Coinbase API.

    Args:
        redis_url: Redis connection URL.
        publisher: RedisPublisher for publishing fills and snapshots.
        include_route_b: Whether to simulate Route B.
        repo: Optional TunerRepository for persisting fills to PostgreSQL.
            When provided, every fill (entry and SL/TP exit) is written to
            the fills table for historical tracking.
    """
    # Restore portfolio state from persisted fill history so equity and
    # positions survive container restarts.  Falls back to a fresh portfolio
    # on any DB error so a missing or empty DB never blocks startup.
    async def _build_portfolio(route: Route) -> PaperPortfolio:
        if repo is not None:
            try:
                fill_records = await repo.get_all_fills(route.value)
                if fill_records:
                    portfolio = PaperPortfolio.restore_from_fills(route, fill_records)
                    open_instruments = [
                        i for i, p in portfolio.positions.items() if p.size > 0
                    ]
                    logger.info(
                        "paper_portfolio_restored",
                        route=route.value,
                        fills_replayed=len(fill_records),
                        realized_pnl=str(portfolio.realized_pnl),
                        fees_paid=str(portfolio.fees_paid),
                        open_positions=open_instruments,
                    )
                    return portfolio
            except Exception as exc:
                logger.warning(
                    "paper_portfolio_restore_failed",
                    route=route.value,
                    error=str(exc),
                )
        return PaperPortfolio(target=route, initial_equity=PAPER_INITIAL_EQUITY)

    portfolios: dict[Route, PaperPortfolio] = {
        Route.A: await _build_portfolio(Route.A),
    }

    if include_route_b:
        portfolios[Route.B] = await _build_portfolio(Route.B)

    # Per-instrument market data cache
    mark_prices: dict[str, Decimal] = {}
    funding_rates: dict[str, Decimal] = {}
    has_market_data = False

    # Pending SL/TP orders waiting to be triggered by price movement
    pending_orders: list[PendingProtectiveOrder] = []

    # --- Consumer: market data ---
    market_consumer = RedisConsumer(redis_url=redis_url, block_ms=2000)
    await market_consumer.subscribe(
        [Channel.MARKET_SNAPSHOTS],
        group="paper_sim_market",
        consumer_name="paper_sim_market_0",
    )

    # --- Consumer: approved orders ---
    order_channels = [Channel.approved_orders(Route.A)]
    if include_route_b:
        order_channels.append(Channel.approved_orders(Route.B))

    order_consumer = RedisConsumer(redis_url=redis_url, block_ms=2000)
    await order_consumer.subscribe(
        order_channels,
        group="paper_sim_exec",
        consumer_name="paper_sim_exec_0",
    )

    # --- Task: cache latest market data ---
    async def market_data_reader() -> None:
        nonlocal has_market_data
        async for channel, msg_id, payload in market_consumer.listen():
            try:
                instrument = payload.get("instrument", "")
                if instrument:
                    mark_prices[instrument] = Decimal(payload["mark_price"])
                    fr = payload.get("funding_rate")
                    if fr is not None:
                        funding_rates[instrument] = Decimal(str(fr))
                    has_market_data = True
            except Exception:
                pass
            await market_consumer.ack(channel, "paper_sim_market", msg_id)

    # --- Task: process orders and simulate fills ---
    async def order_processor() -> None:
        async for channel, msg_id, payload in order_consumer.listen():
            if not has_market_data:
                logger.warning("paper_no_market_data", msg="skipping order, waiting for market data")
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            try:
                order = deserialize_proposed_order(payload)
            except Exception as e:
                logger.error("paper_deserialize_error", error=str(e))
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            portfolio = portfolios.get(order.route)
            if portfolio is None:
                logger.warning("paper_no_portfolio", target=order.route.value)
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

            # Fill at limit price (if set and order is LIMIT), otherwise instrument mark price
            instrument_mark = mark_prices.get(order.instrument, Decimal("0"))
            is_maker = order.order_type == OrderType.LIMIT
            fill_price = (
                order.limit_price
                if is_maker and order.limit_price
                else instrument_mark
            )

            if fill_price == 0:
                logger.warning("paper_no_instrument_price", instrument=order.instrument)
                await order_consumer.ack(channel, "paper_sim_exec", msg_id)
                continue

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

            # Persist entry fill to PostgreSQL
            await _persist_fill(repo, fill)

            # Publish updated snapshot
            snapshot = portfolio.build_snapshot(mark_prices)
            state_ch = Channel.portfolio_state(portfolio.target)
            await publisher.publish(state_ch, portfolio_snapshot_to_dict(snapshot))

            logger.info(
                "paper_fill",
                portfolio=portfolio.target.value,
                instrument=order.instrument,
                side=order.side.value,
                size=str(fill.size),
                price=str(fill.price),
                fee=str(fill.fee_usdc),
                equity=str(snapshot.equity_usdc),
                positions=len(snapshot.open_positions),
            )

            # Register SL/TP protective orders for this fill
            close_side = (
                OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
            )
            if order.stop_loss is not None:
                pending_orders.append(PendingProtectiveOrder(
                    order_id=f"sl-{order.order_id}",
                    instrument=order.instrument,
                    route=order.route,
                    side=close_side,
                    size=order.size,
                    trigger_price=order.stop_loss,
                    fill_price=order.stop_loss,
                    is_stop_loss=True,
                ))
                logger.info(
                    "paper_sl_registered",
                    order_id=order.order_id,
                    instrument=order.instrument,
                    trigger=str(order.stop_loss),
                )
            if order.take_profit is not None:
                pending_orders.append(PendingProtectiveOrder(
                    order_id=f"tp-{order.order_id}",
                    instrument=order.instrument,
                    route=order.route,
                    side=close_side,
                    size=order.size,
                    trigger_price=order.take_profit,
                    fill_price=order.take_profit,
                    is_stop_loss=False,
                ))
                logger.info(
                    "paper_tp_registered",
                    order_id=order.order_id,
                    instrument=order.instrument,
                    trigger=str(order.take_profit),
                )

            await order_consumer.ack(channel, "paper_sim_exec", msg_id)

    # --- Task: publish periodic snapshots ---
    async def periodic_snapshots() -> None:
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            if not has_market_data:
                continue
            for portfolio in portfolios.values():
                snapshot = portfolio.build_snapshot(mark_prices)
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

            if not has_market_data:
                continue

            for portfolio in portfolios.values():
                for instrument in list(portfolio.positions.keys()):
                    rate = funding_rates.get(instrument, Decimal("0"))
                    price = mark_prices.get(instrument, Decimal("0"))
                    if price == 0:
                        continue
                    payment = portfolio.apply_funding(instrument, rate, price)
                    if payment is not None:
                        ch = Channel.funding_payments(portfolio.target)
                        await publisher.publish(ch, funding_payment_to_dict(payment))
                        logger.info(
                            "paper_funding",
                            portfolio=portfolio.target.value,
                            instrument=instrument,
                            rate=str(rate),
                            payment=str(payment.payment_usdc),
                            cumulative=str(payment.cumulative_24h_usdc),
                        )

    # --- Task: check pending SL/TP orders against mark prices ---
    async def protective_order_monitor() -> None:
        """Check pending stop-loss and take-profit orders every second.

        When a mark price crosses a trigger, fill the protective order at its
        limit price (not the current mark), then remove stale orders for the
        same instrument/portfolio if the position was fully closed.
        """
        while True:
            await asyncio.sleep(1)
            if not has_market_data or not pending_orders:
                continue

            triggered: list[PendingProtectiveOrder] = []
            for order in pending_orders:
                price = mark_prices.get(order.instrument)
                if price is not None and order.is_triggered(price):
                    triggered.append(order)

            if not triggered:
                continue

            # Remove all triggered orders upfront to avoid list mutation issues
            triggered_set = set(id(o) for o in triggered)
            pending_orders[:] = [
                o for o in pending_orders if id(o) not in triggered_set
            ]

            # Process triggers — prefer TP over SL when both fire
            # (sort SL=True last so TP executes first)
            triggered.sort(key=lambda o: o.is_stop_loss)

            for order in triggered:
                portfolio = portfolios.get(order.route)
                if portfolio is None:
                    continue

                # Check if there's still a position to close
                pos = portfolio.positions.get(order.instrument)
                if pos is None or pos.size == 0:
                    continue

                # Close size is the smaller of order size and current position
                close_size = min(order.size, pos.size)

                fill = portfolio.apply_fill(
                    order_id=order.order_id,
                    instrument=order.instrument,
                    side=order.side,
                    size=close_size,
                    fill_price=order.fill_price,
                    is_maker=not order.is_stop_loss,
                )

                events_ch = Channel.exchange_events(portfolio.target)
                await publisher.publish(events_ch, fill_to_dict(fill))

                # Persist SL/TP exit fill to PostgreSQL
                await _persist_fill(repo, fill)

                snapshot = portfolio.build_snapshot(mark_prices)
                state_ch = Channel.portfolio_state(portfolio.target)
                await publisher.publish(state_ch, portfolio_snapshot_to_dict(snapshot))

                label = "SL" if order.is_stop_loss else "TP"
                logger.info(
                    f"paper_{label.lower()}_triggered",
                    portfolio=portfolio.target.value,
                    instrument=order.instrument,
                    side=order.side.value,
                    size=str(close_size),
                    fill_price=str(order.fill_price),
                    trigger_price=str(order.trigger_price),
                    realized_pnl=str(portfolio.realized_pnl),
                    equity=str(snapshot.equity_usdc),
                )

                # If position is now closed, cancel remaining pending
                # SL/TP for this instrument+portfolio
                remaining_pos = portfolio.positions.get(order.instrument)
                if remaining_pos is None or remaining_pos.size == 0:
                    pending_orders[:] = [
                        o for o in pending_orders
                        if not (
                            o.instrument == order.instrument
                            and o.route == order.route
                        )
                    ]

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
            tg.create_task(protective_order_monitor())
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error("paper_simulator_error", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        await market_consumer.close()
        await order_consumer.close()
        logger.info("paper_simulator_stopped")
