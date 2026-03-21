"""Telegram bot for trade confirmations and portfolio commands.

Wraps python-telegram-bot v21+ Application to provide:
  - Inline-keyboard confirmations for Portfolio B orders
  - Informational notifications for Portfolio A auto-trades
  - /status command for portfolio overview
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from libs.common.logging import setup_logging
from libs.common.models.order import ApprovedOrder, ProposedOrder

from agents.confirmation.message_composer import (
    compose_expiry_notice,
    compose_trade_request,
)
from agents.confirmation.state_machine import OrderStateMachine

logger = setup_logging("telegram_bot", json_output=False)

# Callback data prefixes
_APPROVE = "approve"
_REJECT = "reject"
_DELAY = "delay"


def _build_keyboard(order_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for order confirmation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"{_APPROVE}:{order_id}"),
            InlineKeyboardButton("Reject", callback_data=f"{_REJECT}:{order_id}"),
        ],
        [
            InlineKeyboardButton("Delay 15m", callback_data=f"{_DELAY}:{order_id}:15"),
            InlineKeyboardButton("Delay 30m", callback_data=f"{_DELAY}:{order_id}:30"),
        ],
    ])


class TelegramBot:
    """Manages the Telegram bot lifecycle and message sending.

    The bot discovers its target chat ID in one of two ways:
      1. Pre-configured via the ``chat_id`` constructor argument (from env).
      2. Learned at runtime when a user sends ``/start``.

    Until a chat ID is available, outbound messages (confirmations,
    notifications) are queued and flushed once the chat is known.

    Args:
        token: Telegram bot token.
        chat_id: Optional pre-configured chat ID. If empty, the bot waits
            for the user to send /start.
        state_machine: Shared order state machine.
        on_approve: Async callback invoked with ApprovedOrder when user approves.
        on_reject: Async callback invoked with order_id when user rejects.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        state_machine: OrderStateMachine,
        on_approve: Any = None,
        on_reject: Any = None,
    ) -> None:
        self._chat_id: str | None = chat_id or None
        self._sm = state_machine
        self._on_approve = on_approve
        self._on_reject = on_reject
        # Map order_id -> telegram message_id for editing
        self._message_map: dict[str, int] = {}
        self._sequence = 0
        # Event set once a chat_id is available
        self._chat_ready = asyncio.Event()
        if self._chat_id:
            self._chat_ready.set()

        self._app = (
            Application.builder()
            .token(token)
            .build()
        )
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("start", self._handle_start))

    @property
    def is_chat_registered(self) -> bool:
        """True once a target chat ID has been established."""
        return self._chat_id is not None

    async def start(self) -> None:
        """Initialize the bot and start polling for updates."""
        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling(drop_pending_updates=True)
        if self._chat_id:
            logger.info("telegram_bot_started", chat_id=self._chat_id)
            try:
                await self._app.bot.send_message(
                    chat_id=self._chat_id,
                    text=(
                        "phantom-perp confirmation bot online.\n"
                        "Monitoring Portfolio B trade proposals.\n\n"
                        "Commands:\n"
                        "  /status - Show pending orders"
                    ),
                )
            except Exception as e:
                logger.warning("startup_greeting_failed", error=str(e))
        else:
            logger.info(
                "telegram_bot_started_awaiting_registration",
                hint="Send /start to the bot to register this chat",
            )

    async def stop(self) -> None:
        """Stop polling and shut down the bot."""
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("telegram_bot_stopped")

    async def _ensure_chat(self) -> str:
        """Block until a chat ID is available, then return it."""
        await self._chat_ready.wait()
        assert self._chat_id is not None
        return self._chat_id

    async def send_confirmation(self, order: ProposedOrder) -> None:
        """Send a trade confirmation message with inline keyboard."""
        chat_id = await self._ensure_chat()
        self._sequence += 1
        text = compose_trade_request(order, sequence_number=self._sequence)
        keyboard = _build_keyboard(order.order_id)

        msg = await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
        self._message_map[order.order_id] = msg.message_id
        logger.info(
            "confirmation_sent",
            order_id=order.order_id,
            message_id=msg.message_id,
        )

    async def send_notification(self, text: str) -> None:
        """Send a plain informational message (no keyboard)."""
        chat_id = await self._ensure_chat()
        await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
        )

    async def send_expiry_notice(self, order: ProposedOrder) -> None:
        """Notify user that an order expired."""
        text = compose_expiry_notice(order)
        await self.send_notification(text)

        # Edit original message to show expired status
        msg_id = self._message_map.pop(order.order_id, None)
        if msg_id:
            await self._edit_message_status(msg_id, order, "EXPIRED")

    async def _handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Process inline keyboard button presses."""
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()

        parts = query.data.split(":")
        action = parts[0]
        order_id = parts[1] if len(parts) > 1 else ""

        if action == _APPROVE:
            approved = self._sm.approve(order_id)
            if approved is None:
                await query.edit_message_text("Order no longer available.")
                return

            await query.edit_message_text(
                query.message.text + "\n\n--- APPROVED ---" if query.message else "APPROVED",
            )
            logger.info("order_approved_by_user", order_id=order_id)
            self._message_map.pop(order_id, None)

            if self._on_approve:
                await self._on_approve(approved)

        elif action == _REJECT:
            ok = self._sm.reject(order_id)
            if not ok:
                await query.edit_message_text("Order no longer available.")
                return

            await query.edit_message_text(
                query.message.text + "\n\n--- REJECTED ---" if query.message else "REJECTED",
            )
            logger.info("order_rejected_by_user", order_id=order_id)
            self._message_map.pop(order_id, None)

            if self._on_reject:
                await self._on_reject(order_id)

        elif action == _DELAY:
            delay_minutes = int(parts[2]) if len(parts) > 2 else 30
            ok = self._sm.delay(order_id, timedelta(minutes=delay_minutes))
            if not ok:
                await query.edit_message_text("Order no longer available.")
                return

            await query.edit_message_text(
                (query.message.text or "")
                + f"\n\n--- DELAYED {delay_minutes}m ---"
                + "\nWill re-send when delay expires.",
            )
            logger.info(
                "order_delayed_by_user",
                order_id=order_id,
                delay_minutes=delay_minutes,
            )

    async def _handle_status(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /status command."""
        pending = self._sm.pending_orders
        if not pending:
            text = "No pending orders.\nUse /status with the dashboard for portfolio details."
        else:
            lines = [f"Pending orders: {len(pending)}"]
            for p in pending:
                direction = "LONG" if p.order.side.value == "BUY" else "SHORT"
                lines.append(
                    f"  {p.order.order_id[:8]}: {p.order.instrument} "
                    f"{direction} {p.order.size} ETH "
                    f"(conviction {p.order.conviction:.2f})"
                )
            text = "\n".join(lines)

        if update.effective_message:
            await update.effective_message.reply_text(text)

    async def _handle_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /start command — registers this chat for confirmations."""
        if not update.effective_chat or not update.effective_message:
            return

        new_chat_id = str(update.effective_chat.id)
        was_registered = self._chat_id is not None

        self._chat_id = new_chat_id
        self._chat_ready.set()

        if was_registered:
            await update.effective_message.reply_text(
                "Chat re-registered for phantom-perp confirmations.\n"
                "Trade proposals for Portfolio B will be sent here."
            )
        else:
            logger.info("chat_registered", chat_id=new_chat_id)
            await update.effective_message.reply_text(
                "phantom-perp confirmation bot active.\n"
                "Trade proposals for Portfolio B will be sent here.\n\n"
                "Commands:\n"
                "  /status - Show pending orders"
            )

    async def _edit_message_status(
        self,
        message_id: int,
        order: ProposedOrder,
        status: str,
    ) -> None:
        """Edit an existing message to show final status."""
        if not self._chat_id:
            return
        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=self._chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except Exception:
            pass  # Message may already be edited
