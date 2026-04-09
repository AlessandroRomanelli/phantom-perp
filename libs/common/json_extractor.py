"""Shared JSON extraction utility for parsing structured data from Claude CLI output.

Claude CLI returns plain text, not structured tool_use blocks. This module bridges
the gap by reliably extracting JSON from markdown-fenced ```json blocks.

Usage:
    from libs.common.json_extractor import extract_json, JsonExtractionError

    try:
        data = extract_json(cli_stdout)
    except JsonExtractionError as e:
        logger.error("json_extraction_failed", error=str(e))
"""

from __future__ import annotations

import json
import re
from typing import Any


class JsonExtractionError(Exception):
    """Raised when no valid JSON block can be extracted from the input text.

    This is a parsing utility error, not a trading system error — it does NOT
    subclass PhantomPerpError.
    """


# Non-greedy match to avoid catastrophic backtracking on large inputs (T-26-02).
# Pattern matches ```json ... ``` blocks, capturing the content between the fences.
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

# Limit on input snippet length shown in error messages.
_SNIPPET_MAX_LEN = 80


def extract_json(text: str) -> dict[str, Any] | list[Any]:
    """Extract and parse the first valid JSON block from markdown-fenced text.

    Searches for the first ```json ... ``` code fence in ``text`` and parses
    its contents with :func:`json.loads`.  Extraneous prose before and after
    the fence is silently ignored.

    Args:
        text: Arbitrary text that may contain one or more ```json fenced blocks,
              typically the stdout of a Claude CLI subprocess call.

    Returns:
        A parsed Python dict or list corresponding to the first valid JSON block.

    Raises:
        JsonExtractionError: When no fenced block is found, when the fenced
            block is empty, or when the block contains malformed JSON.
    """
    snippet = text[:_SNIPPET_MAX_LEN].replace("\n", " ")
    matches = _JSON_FENCE_RE.findall(text)

    if not matches:
        raise JsonExtractionError(
            f"No JSON fenced block (```json ... ```) found in input. "
            f"Input snippet: '{snippet}'"
        )

    for raw in matches:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            result: dict[str, Any] | list[Any] = json.loads(stripped)
            return result
        except json.JSONDecodeError as exc:
            raise JsonExtractionError(
                f"Invalid JSON in fenced block: {exc}. "
                f"Input snippet: '{snippet}'"
            ) from exc

    # All matches were empty (only whitespace content).
    raise JsonExtractionError(
        f"No JSON fenced block (```json ... ```) found in input. "
        f"Input snippet: '{snippet}'"
    )
