"""Unit tests for libs/tuner/notifier.py.

Coverage:
- TunerNotifier.send() calls Bot.send_message with composed report
- Skips silently when TELEGRAM_BOT_TOKEN is empty/unset (logs warning)
- Catches TelegramError without raising (tuner continues)
- Uses HTML parse mode
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.tuner.audit import ParameterChange
from libs.tuner.notifier import TunerNotifier
from libs.tuner.recommender import TuningResult

T0 = datetime(2026, 3, 29, 0, 0, 0, tzinfo=UTC)


def _make_result(*, summary: str = "All good.", with_changes: bool = False) -> TuningResult:
    changes: list[ParameterChange] = []
    if with_changes:
        changes.append(
            ParameterChange(
                strategy="momentum",
                instrument="ETH-PERP",
                param="min_conviction",
                old_value=0.65,
                new_value=0.72,
                reasoning="Win rate below threshold.",
                timestamp=T0,
            )
        )
    return TuningResult(summary=summary, changes=changes)


class TestTunerNotifierSkipsWhenNoToken:
    """When TELEGRAM_BOT_TOKEN is empty or unset, send() logs a warning and returns."""

    @pytest.mark.asyncio
    async def test_skips_when_token_empty(self) -> None:
        notifier = TunerNotifier(token="", chat_id="12345")
        # Should not raise
        await notifier.send(_make_result())

    @pytest.mark.asyncio
    async def test_skips_when_chat_id_empty(self) -> None:
        notifier = TunerNotifier(token="fake-token", chat_id="")
        # Should not raise
        await notifier.send(_make_result())

    @pytest.mark.asyncio
    async def test_logs_warning_when_skipping(self) -> None:
        notifier = TunerNotifier(token="", chat_id="12345")
        with patch("libs.tuner.notifier._logger") as mock_logger:
            await notifier.send(_make_result())
            mock_logger.warning.assert_called_once()


class TestTunerNotifierSendsMessage:
    """When token and chat_id are set, send() delivers the composed report via Bot."""

    @pytest.mark.asyncio
    async def test_calls_bot_send_message(self) -> None:
        mock_bot_instance = AsyncMock()
        mock_bot_instance.send_message = AsyncMock()
        # Simulate async context manager (__aenter__ returns the bot, __aexit__ cleans up)
        mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
        mock_bot_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("libs.tuner.notifier.Bot", return_value=mock_bot_instance):
            notifier = TunerNotifier(token="fake-token", chat_id="12345")
            await notifier.send(_make_result(with_changes=True))

        mock_bot_instance.send_message.assert_called_once()
        call_kwargs = mock_bot_instance.send_message.call_args
        assert call_kwargs.kwargs["chat_id"] == "12345"
        assert call_kwargs.kwargs["parse_mode"] == "HTML"
        assert isinstance(call_kwargs.kwargs["text"], str)
        assert len(call_kwargs.kwargs["text"]) > 0

    @pytest.mark.asyncio
    async def test_uses_html_parse_mode(self) -> None:
        mock_bot_instance = AsyncMock()
        mock_bot_instance.send_message = AsyncMock()
        mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
        mock_bot_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("libs.tuner.notifier.Bot", return_value=mock_bot_instance):
            notifier = TunerNotifier(token="fake-token", chat_id="12345")
            await notifier.send(_make_result())

        call_kwargs = mock_bot_instance.send_message.call_args
        assert call_kwargs.kwargs["parse_mode"] == "HTML"


class TestTunerNotifierHandlesTelegramError:
    """When Telegram API fails, send() catches the error and logs it."""

    @pytest.mark.asyncio
    async def test_catches_telegram_error(self) -> None:
        from telegram.error import TelegramError

        mock_bot_instance = AsyncMock()
        mock_bot_instance.send_message = AsyncMock(
            side_effect=TelegramError("Network error")
        )
        mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
        mock_bot_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("libs.tuner.notifier.Bot", return_value=mock_bot_instance):
            notifier = TunerNotifier(token="fake-token", chat_id="12345")
            # Must not raise
            await notifier.send(_make_result())

    @pytest.mark.asyncio
    async def test_catches_generic_exception(self) -> None:
        mock_bot_instance = AsyncMock()
        mock_bot_instance.send_message = AsyncMock(
            side_effect=Exception("Unexpected error")
        )
        mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
        mock_bot_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("libs.tuner.notifier.Bot", return_value=mock_bot_instance):
            notifier = TunerNotifier(token="fake-token", chat_id="12345")
            # Must not raise
            await notifier.send(_make_result())

    @pytest.mark.asyncio
    async def test_logs_error_on_failure(self) -> None:
        from telegram.error import TelegramError

        mock_bot_instance = AsyncMock()
        mock_bot_instance.send_message = AsyncMock(
            side_effect=TelegramError("API down")
        )
        mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
        mock_bot_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("libs.tuner.notifier.Bot", return_value=mock_bot_instance),
            patch("libs.tuner.notifier._logger") as mock_logger,
        ):
            notifier = TunerNotifier(token="fake-token", chat_id="12345")
            await notifier.send(_make_result())

        mock_logger.error.assert_called_once()
