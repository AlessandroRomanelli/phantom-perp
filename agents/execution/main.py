"""Execution agent — places orders on Coinbase INTX.

Subscribes to:
  - stream:approved_orders:a  (Portfolio A — immediate, from risk agent)
  - stream:confirmed_orders   (Portfolio B — user-confirmed, from confirmation agent)

Publishes to:
  - stream:exchange_events:a  (Portfolio A fills and order events)
  - stream:exchange_events:b  (Portfolio B fills and order events)

Modes:
  - Paper: simulates immediate fills at market price with realistic fees.
  - Live: routes orders to the correct Coinbase portfolio via CoinbaseClientPool.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import orjson
import redis.asyncio as aioredis

from libs.coinbase.models import OrderResponse
from libs.common.config import get_settings, load_yaml_config
from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder
from libs.common.utils import utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.execution.algo_selector import select_algo
from agents.execution.circuit_breaker import CircuitBreaker
from agents.execution.config import ExecutionConfig, load_execution_config
from agents.execution.order_placer import build_fill_from_response
from agents.execution.retry_handler import evaluate_retry
from agents.execution.stop_loss_manager import (
    build_protective_orders,
    validate_stop_loss_required,
)

logger = setup_logging("execution_agent", json_output=False)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def deserialize_proposed_order(payload: dict[str, Any]) -> ProposedOrder:
    """Reconstruct a ProposedOrder from stream:approved_orders:a payload.

    This is the format produced by risk agent's order_to_dict().
    """
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
        reduce_only=payload["reduce_only"] == "True" if isinstance(payload["reduce_only"], str) else bool(payload["reduce_only"]),
        status=OrderStatus(payload["status"]),
        reasoning=payload.get("reasoning", ""),
    )


def deserialize_approved_order(payload: dict[str, Any]) -> ApprovedOrder:
    """Reconstruct an ApprovedOrder from stream:confirmed_orders payload.

    This is the format produced by confirmation agent's approved_order_to_dict().
    """
    return ApprovedOrder(
        order_id=payload["order_id"],
        portfolio_target=PortfolioTarget(payload["portfolio_target"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        order_type=OrderType(payload["order_type"]),
        limit_price=Decimal(payload["limit_price"]) if payload.get("limit_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        leverage=Decimal(payload["leverage"]),
        reduce_only=payload["reduce_only"] == "True" if isinstance(payload["reduce_only"], str) else bool(payload["reduce_only"]),
        approved_at=datetime.fromisoformat(payload["approved_at"]),
    )


def fill_to_dict(fill: Fill) -> dict[str, Any]:
    """Serialize a Fill to a dict for publishing to stream:exchange_events:*."""
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


def deserialize_fill(payload: dict[str, Any]) -> Fill:
    """Reconstruct a Fill from stream:exchange_events:* payload.

    Used by the reconciliation agent.
    """
    return Fill(
        fill_id=payload["fill_id"],
        order_id=payload["order_id"],
        portfolio_target=PortfolioTarget(payload["portfolio_target"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        price=Decimal(payload["price"]),
        fee_usdc=Decimal(payload["fee_usdc"]),
        is_maker=payload["is_maker"] == "True" if isinstance(payload["is_maker"], str) else bool(payload["is_maker"]),
        filled_at=datetime.fromisoformat(payload["filled_at"]),
        trade_id=payload["trade_id"],
    )


# ---------------------------------------------------------------------------
# Market data — read latest snapshot from Redis on demand
# ---------------------------------------------------------------------------


@dataclass
class LatestMarket:
    """Latest market data for execution decisions."""

    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    last_price: Decimal | None = None


async def fetch_latest_market(
    redis: aioredis.Redis,
    instrument: str = "",
) -> LatestMarket:
    """Read the latest market snapshot for a specific instrument.

    Scans recent entries in stream:market_snapshots and returns the first
    matching the requested instrument. Falls back to the absolute latest
    entry if instrument is empty.
    """
    market = LatestMarket()
    # Scan enough entries to find the target instrument (snapshots cycle through 5 instruments)
    count = 1 if not instrument else 20
    entries = await redis.xrevrange(Channel.MARKET_SNAPSHOTS, count=count)
    if not entries:
        return market
    for _, fields in entries:
        raw = fields.get(b"data")
        if raw is None:
            continue
        data = orjson.loads(raw)
        if instrument and data.get("instrument") != instrument:
            continue
        if data.get("best_bid"):
            market.best_bid = Decimal(data["best_bid"])
        if data.get("best_ask"):
            market.best_ask = Decimal(data["best_ask"])
        if data.get("last_price"):
            market.last_price = Decimal(data["last_price"])
        return market
    return market


# ---------------------------------------------------------------------------
# Paper broker — simulates fills for paper trading
# ---------------------------------------------------------------------------


class PaperBroker:
    """Simulates order execution in paper mode.

    Primary orders (MARKET, LIMIT) fill immediately at market price.
    Protective orders (STOP_MARKET, STOP_LIMIT) are "placed" as OPEN.
    Fees are computed at VIP 1 rates (maker 0.0125%, taker 0.0250%).
    """

    def __init__(self) -> None:
        self._counter = 0

    async def create_order(
        self,
        instrument_id: str,
        side: str,
        size: Decimal,
        order_type: str = "LIMIT",
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str = "",
        reduce_only: bool = False,
        *,
        last_price: Decimal | None = None,
    ) -> OrderResponse:
        """Simulate order placement with immediate fill."""
        self._counter += 1
        oid = f"paper-{self._counter:06d}"

        # Protective orders: place but don't fill
        if order_type in ("STOP_MARKET", "STOP_LIMIT"):
            return OrderResponse(
                order_id=oid,
                client_order_id=client_order_id,
                product_id=instrument_id,
                side=side,
                order_type=order_type,
                base_size=str(size),
                limit_price=str(limit_price) if limit_price else "0",
                status="OPEN",
                created_time=utc_now().isoformat(),
            )

        # Fill price and fee rate
        if order_type == "MARKET":
            fill_price = last_price or Decimal("0")
            fee_rate = FEE_TAKER
        else:
            fill_price = limit_price or last_price or Decimal("0")
            fee_rate = FEE_MAKER

        notional = size * fill_price
        fee = (notional * fee_rate).quantize(Decimal("0.01"))

        return OrderResponse(
            order_id=oid,
            client_order_id=client_order_id,
            product_id=instrument_id,
            side=side,
            order_type=order_type,
            base_size=str(size),
            limit_price=str(limit_price) if limit_price else "0",
            status="FILLED",
            filled_size=str(size),
            filled_value=str(notional),
            average_filled_price=str(fill_price),
            total_fees=str(fee),
            created_time=utc_now().isoformat(),
        )

    async def cancel_order(self, order_id: str) -> None:
        """No-op in paper mode."""

    async def close(self) -> None:
        """No-op."""


# ---------------------------------------------------------------------------
# Core execution logic
# ---------------------------------------------------------------------------


async def _place_order(
    is_paper: bool,
    paper_broker: PaperBroker | None,
    portfolio_target: PortfolioTarget,
    instrument: str,
    side: str,
    size: Decimal,
    order_type: str,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    client_order_id: str,
    reduce_only: bool,
    last_price: Decimal | None,
) -> OrderResponse:
    """Route order placement to the correct broker."""
    if is_paper:
        assert paper_broker is not None
        return await paper_broker.create_order(
            instrument_id=instrument,
            side=side,
            size=size,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            last_price=last_price,
        )
    # Live mode: import and use CoinbaseClientPool
    # (deferred import so paper mode doesn't require API credentials)
    from libs.coinbase.auth import CoinbaseAuth
    from libs.coinbase.client_pool import CoinbaseClientPool

    raise NotImplementedError(
        "Live execution not yet implemented — use paper mode"
    )


async def _execute_with_retry(
    *,
    order_id: str,
    portfolio_target: PortfolioTarget,
    instrument: str,
    side: OrderSide,
    size: Decimal,
    order_type: OrderType,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    reduce_only: bool,
    exec_plan_limit_price: Decimal | None,
    exec_plan_order_type: OrderType,
    is_maker: bool,
    config: ExecutionConfig,
    is_paper: bool,
    paper_broker: PaperBroker | None,
    last_price: Decimal | None,
) -> tuple[OrderResponse | None, bool]:
    """Place an order with retry logic on failure.

    Returns:
        (response, is_maker) on success; (None, is_maker) on exhausted retries.
    """
    from agents.execution.algo_selector import ExecutionPlan

    current_plan = ExecutionPlan(
        order_type=exec_plan_order_type,
        limit_price=exec_plan_limit_price,
        is_maker=is_maker,
    )

    for attempt in range(config.max_retries + 1):
        try:
            response = await _place_order(
                is_paper=is_paper,
                paper_broker=paper_broker,
                portfolio_target=portfolio_target,
                instrument=instrument,
                side=side.value,
                size=size,
                order_type=current_plan.order_type.value,
                limit_price=current_plan.limit_price,
                stop_price=stop_price,
                client_order_id=order_id,
                reduce_only=reduce_only,
                last_price=last_price,
            )
            return response, current_plan.is_maker

        except Exception as e:
            decision = evaluate_retry(e, attempt, config, current_plan)
            if not decision.should_retry:
                logger.error(
                    "order_placement_failed",
                    order_id=order_id,
                    attempt=attempt + 1,
                    reason=decision.reason,
                    error=str(e),
                )
                return None, current_plan.is_maker

            logger.warning(
                "order_retry",
                order_id=order_id,
                attempt=attempt + 1,
                reason=decision.reason,
                wait=decision.wait_seconds,
            )
            if decision.wait_seconds > 0:
                await asyncio.sleep(decision.wait_seconds)
            if decision.adjusted_plan:
                current_plan = decision.adjusted_plan

    return None, current_plan.is_maker


async def _place_protective_orders(
    *,
    order_id: str,
    portfolio_target: PortfolioTarget,
    instrument: str,
    fill_side: OrderSide,
    fill_size: Decimal,
    fill_price: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    is_paper: bool,
    paper_broker: PaperBroker | None,
    last_price: Decimal | None,
) -> None:
    """Place stop-loss and take-profit orders after a primary fill."""
    protective = build_protective_orders(
        fill_side=fill_side,
        fill_size=fill_size,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
    )

    if protective.stop_loss:
        sl = protective.stop_loss
        try:
            await _place_order(
                is_paper=is_paper,
                paper_broker=paper_broker,
                portfolio_target=portfolio_target,
                instrument=instrument,
                side=sl.side.value,
                size=sl.size,
                order_type=sl.order_type.value,
                limit_price=sl.limit_price,
                stop_price=sl.stop_price,
                client_order_id=f"sl-{order_id}",
                reduce_only=sl.reduce_only,
                last_price=last_price,
            )
            logger.info(
                "stop_loss_placed",
                order_id=order_id,
                stop_price=str(sl.stop_price),
            )
        except Exception as e:
            logger.error(
                "stop_loss_placement_failed",
                order_id=order_id,
                error=str(e),
            )

    if protective.take_profit:
        tp = protective.take_profit
        try:
            await _place_order(
                is_paper=is_paper,
                paper_broker=paper_broker,
                portfolio_target=portfolio_target,
                instrument=instrument,
                side=tp.side.value,
                size=tp.size,
                order_type=tp.order_type.value,
                limit_price=tp.limit_price,
                stop_price=tp.stop_price,
                client_order_id=f"tp-{order_id}",
                reduce_only=tp.reduce_only,
                last_price=last_price,
            )
            logger.info(
                "take_profit_placed",
                order_id=order_id,
                target_price=str(tp.limit_price),
            )
        except Exception as e:
            logger.error(
                "take_profit_placement_failed",
                order_id=order_id,
                error=str(e),
            )


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------


async def run_agent() -> None:
    """Main event loop for the execution agent.

    1. Subscribes to stream:approved_orders:a and stream:confirmed_orders
    2. Receives orders (ProposedOrder for A, ApprovedOrder for B)
    3. Checks circuit breaker for target portfolio
    4. Fetches latest market data for limit price computation
    5. Selects execution algorithm (LIMIT preferred for maker fees)
    6. Places orders (paper: simulated fill, live: Coinbase API)
    7. Handles retries on rejection
    8. Places protective SL/TP orders after fill
    9. Publishes fills to stream:exchange_events:a or :b
    """
    settings = get_settings()
    yaml_config = load_yaml_config("default")
    exec_config = load_execution_config(yaml_config)

    is_paper = settings.infra.environment == "paper"
    paper_broker = PaperBroker() if is_paper else None

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)
    redis = aioredis.from_url(settings.infra.redis_url, decode_responses=False)

    cb = CircuitBreaker()

    channel_a = Channel.approved_orders(PortfolioTarget.A)
    channel_b = Channel.confirmed_orders()

    await consumer.subscribe(
        channels=[channel_a, channel_b],
        group="execution_agent",
        consumer_name="execution-0",
    )

    logger.info(
        "execution_agent_started",
        mode="paper" if is_paper else "live",
        prefer_maker=exec_config.prefer_maker,
        max_retries=exec_config.max_retries,
        order_ttl_seconds=exec_config.order_ttl.total_seconds(),
        subscribe_a=channel_a,
        subscribe_b=channel_b,
    )

    order_count = 0
    fill_count = 0

    try:
        async for channel, msg_id, payload in consumer.listen():
            try:
                # -----------------------------------------------------------
                # 1. Deserialize the order based on which stream it came from
                # -----------------------------------------------------------
                if channel == channel_a:
                    proposed = deserialize_proposed_order(payload)
                    order_id = proposed.order_id
                    portfolio_target = proposed.portfolio_target
                    instrument = proposed.instrument
                    side = proposed.side
                    size = proposed.size
                    order_type = proposed.order_type
                    limit_price = proposed.limit_price
                    stop_loss = proposed.stop_loss
                    take_profit = proposed.take_profit
                    reduce_only = proposed.reduce_only
                    source_label = "risk_agent"
                elif channel == channel_b:
                    approved = deserialize_approved_order(payload)
                    order_id = approved.order_id
                    portfolio_target = approved.portfolio_target
                    instrument = approved.instrument
                    side = approved.side
                    size = approved.size
                    order_type = approved.order_type
                    limit_price = approved.limit_price
                    stop_loss = approved.stop_loss
                    take_profit = approved.take_profit
                    reduce_only = approved.reduce_only
                    source_label = "confirmation_agent"
                else:
                    await consumer.ack(channel, "execution_agent", msg_id)
                    continue

                order_count += 1
                direction = "LONG" if side == OrderSide.BUY else "SHORT"
                logger.info(
                    "order_received",
                    order_id=order_id,
                    portfolio=portfolio_target.value,
                    direction=direction,
                    size=str(size),
                    source=source_label,
                    count=order_count,
                )

                # -----------------------------------------------------------
                # 2. Check circuit breaker
                # -----------------------------------------------------------
                if cb.is_open(portfolio_target):
                    trip = cb.get_trip(portfolio_target)
                    logger.warning(
                        "order_blocked_circuit_breaker",
                        order_id=order_id,
                        portfolio=portfolio_target.value,
                        reason=trip.reason if trip else "unknown",
                    )
                    await consumer.ack(channel, "execution_agent", msg_id)
                    continue

                # -----------------------------------------------------------
                # 3. Fetch latest market data for limit price computation
                # -----------------------------------------------------------
                market = await fetch_latest_market(redis, instrument)
                if market.last_price is None:
                    logger.warning(
                        "no_market_data_skipping",
                        order_id=order_id,
                    )
                    await consumer.ack(channel, "execution_agent", msg_id)
                    continue

                # -----------------------------------------------------------
                # 4. Select execution algorithm
                # -----------------------------------------------------------
                plan = select_algo(
                    side=side,
                    requested_type=order_type,
                    best_bid=market.best_bid,
                    best_ask=market.best_ask,
                    limit_offset_bps=exec_config.limit_offset_bps,
                    prefer_maker=exec_config.prefer_maker,
                    explicit_limit_price=limit_price,
                )

                logger.info(
                    "execution_plan",
                    order_id=order_id,
                    algo_type=plan.order_type.value,
                    limit_price=str(plan.limit_price) if plan.limit_price else None,
                    is_maker=plan.is_maker,
                    market_bid=str(market.best_bid),
                    market_ask=str(market.best_ask),
                )

                # -----------------------------------------------------------
                # 5. Place the order (with retry logic)
                # -----------------------------------------------------------
                response, was_maker = await _execute_with_retry(
                    order_id=order_id,
                    portfolio_target=portfolio_target,
                    instrument=instrument,
                    side=side,
                    size=size,
                    order_type=order_type,
                    limit_price=limit_price,
                    stop_price=None,
                    reduce_only=reduce_only,
                    exec_plan_limit_price=plan.limit_price,
                    exec_plan_order_type=plan.order_type,
                    is_maker=plan.is_maker,
                    config=exec_config,
                    is_paper=is_paper,
                    paper_broker=paper_broker,
                    last_price=market.last_price,
                )

                if response is None:
                    logger.error(
                        "order_abandoned",
                        order_id=order_id,
                        reason="all_retries_exhausted",
                    )
                    await consumer.ack(channel, "execution_agent", msg_id)
                    continue

                # -----------------------------------------------------------
                # 6. Build fill and publish to exchange_events stream
                # -----------------------------------------------------------
                fill = build_fill_from_response(
                    order_id=order_id,
                    portfolio_target=portfolio_target,
                    response=response,
                    is_maker=was_maker,
                )

                if fill:
                    # In paper mode, the reconciliation paper simulator publishes
                    # fills — skip here to avoid duplicates in exchange_events.
                    if not is_paper:
                        events_channel = Channel.exchange_events(portfolio_target)
                        await publisher.publish(events_channel, fill_to_dict(fill))
                    fill_count += 1

                    logger.info(
                        "order_filled",
                        order_id=order_id,
                        portfolio=portfolio_target.value,
                        direction=direction,
                        size=str(fill.size),
                        price=str(fill.price),
                        fee_usdc=str(fill.fee_usdc),
                        is_maker=fill.is_maker,
                        exchange_order_id=response.order_id,
                        fill_count=fill_count,
                    )

                    # -------------------------------------------------------
                    # 7. Place protective SL/TP orders
                    # -------------------------------------------------------
                    has_sl = stop_loss is not None
                    sl_valid = has_sl and validate_stop_loss_required(
                        stop_loss, side, fill.price,
                    )

                    if not has_sl:
                        logger.warning(
                            "no_stop_loss",
                            order_id=order_id,
                            note="safety rule: every position should have a stop-loss",
                        )
                    elif not sl_valid:
                        logger.warning(
                            "invalid_stop_loss",
                            order_id=order_id,
                            stop_loss=str(stop_loss),
                            fill_price=str(fill.price),
                            side=side.value,
                        )

                    if has_sl or take_profit is not None:
                        await _place_protective_orders(
                            order_id=order_id,
                            portfolio_target=portfolio_target,
                            instrument=instrument,
                            fill_side=side,
                            fill_size=fill.size,
                            fill_price=fill.price,
                            stop_loss=stop_loss if sl_valid else None,
                            take_profit=take_profit,
                            is_paper=is_paper,
                            paper_broker=paper_broker,
                            last_price=market.last_price,
                        )
                else:
                    logger.info(
                        "order_placed_no_fill",
                        order_id=order_id,
                        exchange_status=response.status,
                        exchange_order_id=response.order_id,
                    )

            except (KeyError, ValueError) as e:
                logger.warning(
                    "order_deserialize_error",
                    error=str(e),
                    channel=channel,
                )
            except Exception as e:
                logger.error(
                    "order_processing_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

            await consumer.ack(channel, "execution_agent", msg_id)

            if order_count % 50 == 0:
                logger.info(
                    "execution_progress",
                    orders_processed=order_count,
                    fills=fill_count,
                )
    finally:
        await consumer.close()
        await publisher.close()
        await redis.aclose()
        if paper_broker:
            await paper_broker.close()
        logger.info(
            "execution_agent_stopped",
            orders_processed=order_count,
            fills=fill_count,
        )


def main() -> None:
    """CLI entrypoint."""
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass

    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("execution_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
