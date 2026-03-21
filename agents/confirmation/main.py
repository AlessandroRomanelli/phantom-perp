"""Confirmation agent — Telegram-based trade confirmation for Portfolio B.

Subscribes to stream:approved_orders:b, presents orders to the user
via Telegram, and publishes confirmed orders to stream:confirmed_orders.

Portfolio A orders bypass this agent entirely.

Modes:
  - Paper (auto-approve all): passes orders straight through without Telegram.
  - Live: sends Telegram confirmation with inline keyboard, waits for user.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.config import get_settings, load_yaml_config
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, ProposedOrder
from libs.common.utils import utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.confirmation.config import ConfirmationConfig, load_confirmation_config
from agents.confirmation.state_machine import OrderStateMachine

logger = setup_logging("confirmation", json_output=False)

EXPIRY_CHECK_INTERVAL = 15  # seconds
DELAYED_RESEND_INTERVAL = 30  # seconds


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def deserialize_order(payload: dict[str, Any]) -> ProposedOrder:
    """Reconstruct a ProposedOrder from the dict published by the risk agent."""
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


def approved_order_to_dict(order: ApprovedOrder) -> dict[str, Any]:
    """Serialize an ApprovedOrder for publishing to stream:confirmed_orders."""
    return {
        "order_id": order.order_id,
        "portfolio_target": order.portfolio_target.value,
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

    Used by the execution agent to consume confirmed orders.
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


# ---------------------------------------------------------------------------
# Auto-approve helper
# ---------------------------------------------------------------------------


def _auto_approve(order: ProposedOrder) -> ApprovedOrder:
    """Convert a ProposedOrder to ApprovedOrder without user interaction."""
    return ApprovedOrder(
        order_id=order.order_id,
        portfolio_target=order.portfolio_target,
        instrument=order.instrument,
        side=order.side,
        size=order.size,
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        leverage=order.leverage,
        reduce_only=order.reduce_only,
        approved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------


async def run_agent() -> None:
    """Main event loop for the confirmation agent.

    1. Subscribes to stream:approved_orders:b
    2. Receives risk-approved orders for Portfolio B
    3. Checks auto-approve eligibility
    4. If auto-approvable: immediately publishes to stream:confirmed_orders
    5. Otherwise: sends Telegram message and waits for user response
    6. Periodically expires stale orders
    """
    settings = get_settings()
    yaml_config = load_yaml_config(settings.infra.environment)
    if not yaml_config:
        yaml_config = load_yaml_config("default")
    conf = load_confirmation_config(yaml_config)

    sm = OrderStateMachine(config=conf)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)
    consumer = RedisConsumer(redis_url=settings.infra.redis_url)

    channel_b = Channel.approved_orders(PortfolioTarget.B)
    await consumer.subscribe(
        channels=[channel_b],
        group="confirmation_agent",
        consumer_name="confirmation-0",
    )

    has_telegram = bool(settings.telegram.bot_token)
    bot = None

    if has_telegram:
        from agents.confirmation.bot import TelegramBot

        async def on_approve(approved: ApprovedOrder) -> None:
            await publisher.publish(
                Channel.confirmed_orders(),
                approved_order_to_dict(approved),
            )
            logger.info("confirmed_order_published", order_id=approved.order_id)

        async def on_reject(order_id: str) -> None:
            logger.info("order_rejected", order_id=order_id)

        bot = TelegramBot(
            token=settings.telegram.bot_token,
            chat_id=settings.telegram.chat_id,  # may be empty — bot learns via /start
            state_machine=sm,
            on_approve=on_approve,
            on_reject=on_reject,
        )

    logger.info(
        "confirmation_agent_started",
        mode="telegram" if has_telegram else "auto_approve",
        chat_id_preconfigured=bool(settings.telegram.chat_id),
        ttl_seconds=conf.default_ttl.total_seconds(),
        auto_approve_enabled=conf.auto_approve.enabled,
        channel=channel_b,
    )

    # -- Task: consume orders from Redis -----------------------------------

    async def order_consumer() -> None:
        order_count = 0
        async for channel, msg_id, payload in consumer.listen():
            try:
                order = deserialize_order(payload)
            except (KeyError, ValueError) as e:
                logger.warning("order_deserialize_error", error=str(e))
                await consumer.ack(channel, "confirmation_agent", msg_id)
                continue

            order_count += 1
            logger.info(
                "order_received",
                order_id=order.order_id,
                side=order.side.value,
                size=str(order.size),
                conviction=order.conviction,
                count=order_count,
            )

            # Check auto-approve (paper mode auto-approves everything)
            if sm.check_auto_approve(order):
                approved = _auto_approve(order)
                await publisher.publish(
                    Channel.confirmed_orders(),
                    approved_order_to_dict(approved),
                )
                logger.info("auto_approved", order_id=order.order_id)

                if bot:
                    direction = "LONG" if order.side.value == "BUY" else "SHORT"
                    await bot.send_notification(
                        f"Auto-approved: {order.instrument} {direction} "
                        f"{order.size} ETH (conviction {order.conviction:.2f})"
                    )
            elif bot:
                # Register in state machine and send Telegram confirmation
                sm.receive(order)
                await bot.send_confirmation(order)
            else:
                # No Telegram, no auto-approve — auto-approve as fallback
                approved = _auto_approve(order)
                await publisher.publish(
                    Channel.confirmed_orders(),
                    approved_order_to_dict(approved),
                )
                logger.info(
                    "fallback_auto_approved",
                    order_id=order.order_id,
                    reason="no_telegram_configured",
                )

            await consumer.ack(channel, "confirmation_agent", msg_id)

    # -- Task: periodic expiry check ---------------------------------------

    async def expiry_checker() -> None:
        while True:
            await asyncio.sleep(EXPIRY_CHECK_INTERVAL)
            expired = sm.expire_stale()
            for pending in expired:
                logger.info("order_expired", order_id=pending.order.order_id)
                if bot:
                    await bot.send_expiry_notice(pending.order)
            # Clean up terminal orders from memory
            if expired:
                sm.purge_terminal()

    # -- Task: resend delayed orders ---------------------------------------

    async def delayed_resender() -> None:
        while True:
            await asyncio.sleep(DELAYED_RESEND_INTERVAL)
            if not bot:
                continue
            for pending in sm.actionable_orders:
                # Actionable means delay has expired — resend confirmation
                if pending.delay_until is not None:
                    pending.delay_until = None  # Clear the delay
                    await bot.send_confirmation(pending.order)
                    logger.info(
                        "delayed_order_resent",
                        order_id=pending.order.order_id,
                    )

    # -- Run all tasks concurrently ----------------------------------------

    try:
        if bot:
            await bot.start()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(order_consumer())
            tg.create_task(expiry_checker())
            tg.create_task(delayed_resender())
    finally:
        if bot:
            await bot.stop()
        await consumer.close()
        await publisher.close()
        logger.info("confirmation_agent_stopped")


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
        logger.info("confirmation_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
