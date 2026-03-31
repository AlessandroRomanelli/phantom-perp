"""Telegram notification sender for tuner run results.

Fire-and-forget I/O wrapper around ``telegram.Bot``. Uses the async context
manager pattern from python-telegram-bot v21+ — no Application/polling needed.

Graceful degradation:
- Skips silently when ``TELEGRAM_BOT_TOKEN`` or ``TELEGRAM_CHAT_ID`` is empty/unset.
- Catches ``TelegramError`` (and any other exception) without re-raising, so
  the tuner always completes even if Telegram is unreachable.

Exports:
    TunerNotifier -- async sender wrapping telegram.Bot
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from telegram import Bot
from telegram.error import TelegramError

from libs.tuner.recommender import TuningResult
from libs.tuner.report import compose_tuning_report

_logger = structlog.get_logger(__name__)


class TunerNotifier:
    """Sends tuning run reports to Telegram.

    Args:
        token: Telegram bot token. If empty, send() is a no-op.
        chat_id: Telegram chat ID. If empty, send() is a no-op.
    """

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    async def send(self, result: TuningResult) -> None:
        """Compose and send a tuning report to Telegram.

        Skips silently (with a warning log) if token or chat_id is empty.
        Catches all exceptions to ensure the tuner process is never blocked
        by Telegram failures.

        Args:
            result: The TuningResult from a completed tuning cycle.
        """
        if not self._token or not self._chat_id:
            _logger.warning(
                "tuner_telegram_skipped",
                reason="missing token or chat_id",
            )
            return

        try:
            report = compose_tuning_report(result, datetime.now(UTC))
            async with Bot(self._token) as bot:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=report,
                    parse_mode="HTML",
                )
            _logger.info(
                "tuner_telegram_sent",
                chat_id=self._chat_id,
                changes=len(result.changes),
            )
        except TelegramError as exc:
            _logger.error(
                "tuner_telegram_failed",
                error=str(exc),
                exc_type="TelegramError",
            )
        except Exception as exc:
            _logger.error(
                "tuner_telegram_failed",
                error=str(exc),
                exc_type=type(exc).__name__,
            )
