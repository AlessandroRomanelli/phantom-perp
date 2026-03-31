"""Compose Telegram-friendly HTML reports for tuning run results.

Pure formatting module — no I/O, no Telegram dependency. Produces HTML
strings suitable for Telegram's ``parse_mode="HTML"`` (supports ``<b>``,
``<i>``, ``<code>``).

All Claude-generated text (summary, reasoning) is ``html.escape()``-d to
prevent injection of HTML tags. Reasoning is truncated at 200 chars per
entry to keep total message length under 4096 chars.

Exports:
    compose_tuning_report -- format a TuningResult into an HTML string
"""

from __future__ import annotations

import html
from datetime import datetime

from libs.tuner.audit import ParameterChange
from libs.tuner.recommender import TuningResult

# Telegram message hard limit
_MAX_MESSAGE_LENGTH = 4096

# Per-entry reasoning truncation limit (chars)
_MAX_REASONING_CHARS = 200


def _truncate(text: str, limit: int = _MAX_REASONING_CHARS) -> str:
    """Truncate text to *limit* chars, appending '…' if trimmed."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_change(change: ParameterChange) -> str:
    """Format a single ParameterChange as an HTML block.

    Args:
        change: The parameter change to format.

    Returns:
        HTML snippet for this change.
    """
    if change.instrument is not None:
        label = f"{change.strategy} / {change.instrument}"
    else:
        label = f"{change.strategy} (base)"

    reasoning = html.escape(_truncate(change.reasoning))

    return (
        f"<b>{html.escape(label)}</b>\n"
        f"  {html.escape(change.param)}: {change.old_value} → {change.new_value}\n"
        f"  <i>{reasoning}</i>"
    )


def compose_tuning_report(result: TuningResult, timestamp: datetime) -> str:
    """Compose a Telegram HTML report from a TuningResult.

    Produces two formats:
    - **Changes present**: header + Claude summary + per-param old→new with reasoning.
    - **No changes**: header + explicit no-change message + Claude summary.

    All Claude-generated text is ``html.escape()``-d. Reasoning is truncated
    at 200 chars per entry. If the composed message exceeds 4096 chars,
    change entries are progressively dropped from the end with a count note.

    Args:
        result: The TuningResult from a tuning cycle.
        timestamp: UTC timestamp of the tuning run.

    Returns:
        HTML-formatted string safe for Telegram ``parse_mode="HTML"``.
    """
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M UTC")
    header = f"<b>🔧 Tuner Run — {ts_str}</b>"

    if not result.changes:
        return _compose_no_changes(header, result.summary)

    return _compose_with_changes(header, result.summary, result.changes)


def _compose_no_changes(header: str, summary: str) -> str:
    """Compose the no-change case message."""
    escaped_summary = html.escape(summary) if summary else "See logs for detail."
    return (
        f"{header}\n\n"
        f"<b>No parameter changes.</b>\n"
        f"<i>{escaped_summary}</i>"
    )


def _compose_with_changes(
    header: str,
    summary: str,
    changes: list[ParameterChange],
) -> str:
    """Compose the changes-present case message.

    Builds the full message and truncates change entries if the total
    exceeds 4096 chars.
    """
    escaped_summary = html.escape(summary) if summary else ""

    preamble = (
        f"{header}\n\n"
        f"<b>Claude's Assessment:</b>\n"
        f"<i>{escaped_summary}</i>\n\n"
        f"<b>Changes ({len(changes)}):</b>"
    )

    change_blocks = [_format_change(c) for c in changes]

    # Try full message first
    full_msg = preamble + "\n\n" + "\n\n".join(change_blocks)
    if len(full_msg) <= _MAX_MESSAGE_LENGTH:
        return full_msg

    # Progressive truncation: drop change entries from the end until it fits
    for keep in range(len(change_blocks) - 1, 0, -1):
        omitted = len(change_blocks) - keep
        truncated_msg = (
            preamble
            + "\n\n"
            + "\n\n".join(change_blocks[:keep])
            + f"\n\n<i>… and {omitted} more change(s). See logs for full details.</i>"
        )
        if len(truncated_msg) <= _MAX_MESSAGE_LENGTH:
            return truncated_msg

    # Absolute fallback: just preamble + truncation note
    return preamble + f"\n\n<i>… {len(changes)} change(s). See logs for full details.</i>"
