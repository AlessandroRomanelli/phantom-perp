"""Unit tests for libs/tuner/claude_client.py.

Coverage:
- build_system_prompt: returns string with role and safety constraint references
- build_user_message: contains all 4 required sections (CLAI-03)
- build_user_message: insufficient data entries for None metrics
- DEFAULT_MODEL: stable alias
- call_claude: extracts dict from subprocess stdout JSON code block
- call_claude: passes combined prompt to CLI with correct args and timeout
- call_claude: returns None on subprocess.TimeoutExpired
- call_claude: returns None on non-zero returncode
- call_claude: returns None on invalid JSON stdout
- call_claude: returns None on OSError (claude not found)
- call_claude: returns None when parsed result is not a dict
"""

from __future__ import annotations

import subprocess
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from libs.metrics.engine import StrategyMetrics
from libs.tuner.bounds import BoundsEntry
from libs.tuner.claude_client import (
    DEFAULT_MODEL,
    build_system_prompt,
    build_user_message,
    call_claude,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_metrics(source: str, instrument: str, trade_count: int = 20) -> StrategyMetrics:
    """Build a minimal StrategyMetrics for tests."""
    return StrategyMetrics(
        primary_source=source,
        instrument=instrument,
        trade_count=trade_count,
        win_count=12,
        loss_count=8,
        win_rate=0.6,
        avg_win_usdc=Decimal("30.00"),
        avg_loss_usdc=Decimal("15.00"),
        expectancy_usdc=Decimal("12.00"),
        profit_factor=2.0,
        total_gross_pnl=Decimal("500.00"),
        total_fees_usdc=Decimal("25.00"),
        funding_costs_usdc=Decimal("0"),
        total_net_pnl=Decimal("475.00"),
        max_drawdown_usdc=Decimal("100.00"),
        max_drawdown_duration_hours=4.0,
    )


@pytest.fixture()
def sample_metrics() -> dict[tuple[str, str], StrategyMetrics | None]:
    """Metrics dict with one valid entry and one insufficient-data entry."""
    return {
        ("momentum", "ETH-PERP"): _make_metrics("momentum", "ETH-PERP"),
        ("momentum", "BTC-PERP"): None,  # insufficient data
    }


@pytest.fixture()
def sample_current_params() -> dict[str, dict]:
    """Minimal current params dict."""
    return {
        "momentum": {
            "parameters": {
                "min_conviction": 0.55,
                "route_a_min_conviction": 0.70,
                "cooldown_bars": 5,
            }
        }
    }


@pytest.fixture()
def sample_registry() -> dict[str, BoundsEntry]:
    """Minimal bounds registry with two entries."""
    return {
        "min_conviction": BoundsEntry(
            param_name="min_conviction",
            min_value=0.10,
            max_value=0.90,
            value_type="float",
        ),
        "cooldown_bars": BoundsEntry(
            param_name="cooldown_bars",
            min_value=1.0,
            max_value=30.0,
            value_type="int",
        ),
    }


# ---------------------------------------------------------------------------
# build_system_prompt tests
# ---------------------------------------------------------------------------


def test_build_system_prompt_returns_string() -> None:
    """build_system_prompt() returns a non-empty string."""
    result = build_system_prompt()
    assert isinstance(result, str)
    assert len(result) > 0


def test_build_system_prompt_contains_role_and_rules() -> None:
    """System prompt contains role definition and key safety terms (D-01)."""
    result = build_system_prompt()
    # Role definition
    assert "parameter tuner" in result.lower()
    # Safety constraint reference
    assert "bounds" in result.lower()
    # Reasoning requirement
    assert "reasoning" in result.lower()


# ---------------------------------------------------------------------------
# build_user_message tests
# ---------------------------------------------------------------------------


def test_build_user_message_contains_metrics_section(
    sample_metrics: dict[tuple[str, str], StrategyMetrics | None],
    sample_current_params: dict[str, dict],
    sample_registry: dict[str, BoundsEntry],
) -> None:
    """User message contains Performance Metrics section header (CLAI-03)."""
    result = build_user_message(sample_metrics, sample_current_params, sample_registry)
    assert "## Performance Metrics" in result
    assert "momentum" in result
    assert "ETH-PERP" in result


def test_build_user_message_contains_current_params_section(
    sample_metrics: dict[tuple[str, str], StrategyMetrics | None],
    sample_current_params: dict[str, dict],
    sample_registry: dict[str, BoundsEntry],
) -> None:
    """User message contains Current Parameter Values section (CLAI-03)."""
    result = build_user_message(sample_metrics, sample_current_params, sample_registry)
    assert "## Current Parameter Values" in result
    assert "min_conviction" in result


def test_build_user_message_contains_bounds_section(
    sample_metrics: dict[tuple[str, str], StrategyMetrics | None],
    sample_current_params: dict[str, dict],
    sample_registry: dict[str, BoundsEntry],
) -> None:
    """User message contains Bounds Registry section with min/max values (CLAI-03)."""
    result = build_user_message(sample_metrics, sample_current_params, sample_registry)
    assert "## Bounds Registry" in result
    assert "0.10" in result
    assert "0.90" in result


def test_build_user_message_contains_tunable_params_section(
    sample_metrics: dict[tuple[str, str], StrategyMetrics | None],
    sample_current_params: dict[str, dict],
    sample_registry: dict[str, BoundsEntry],
) -> None:
    """User message contains Tunable Parameters section (CLAI-03)."""
    result = build_user_message(sample_metrics, sample_current_params, sample_registry)
    assert "## Tunable Parameters" in result


def test_build_user_message_insufficient_data_entries(
    sample_metrics: dict[tuple[str, str], StrategyMetrics | None],
    sample_current_params: dict[str, dict],
    sample_registry: dict[str, BoundsEntry],
) -> None:
    """Metrics with None value show 'insufficient data' for that pair (D-03)."""
    result = build_user_message(sample_metrics, sample_current_params, sample_registry)
    # BTC-PERP has None metrics -- should show insufficient data marker
    assert "insufficient data" in result.lower()
    assert "BTC-PERP" in result


# ---------------------------------------------------------------------------
# call_claude tests
# ---------------------------------------------------------------------------


def test_call_claude_returns_tool_input_on_success() -> None:
    """call_claude returns the parsed dict from subprocess stdout JSON block."""
    recs = [
        {
            "strategy": "momentum",
            "instrument": "ETH-PERP",
            "param": "min_conviction",
            "value": 0.6,
            "reasoning": "test",
        }
    ]
    stdout = '```json\n{"summary": "good session", "recommendations": ' + str(recs).replace("'", '"') + '}\n```'
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = stdout
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = call_claude(DEFAULT_MODEL, "system prompt", "user message")

    assert result is not None
    assert result["summary"] == "good session"
    assert len(result["recommendations"]) == 1


def test_call_claude_passes_prompt_to_cli() -> None:
    """call_claude calls subprocess.run(["claude", "-p", ...]) with combined prompt and timeout."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = '```json\n{"summary": "ok", "recommendations": []}\n```'
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result) as mock_run:
        call_claude("model", "sys prompt", "user msg")

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    # Combined prompt contains both system and user parts
    combined_prompt = cmd[2]
    assert "sys prompt" in combined_prompt
    assert "user msg" in combined_prompt
    # Timeout kwarg passed
    assert call_args.kwargs.get("timeout") == 120


def test_call_claude_returns_none_on_timeout() -> None:
    """call_claude returns None when subprocess.TimeoutExpired is raised."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120)):
        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_call_claude_returns_none_on_nonzero_exit() -> None:
    """call_claude returns None when subprocess returncode is non-zero."""
    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stderr = "error output"
    fake_result.stdout = ""

    with patch("subprocess.run", return_value=fake_result):
        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_call_claude_returns_none_on_invalid_json() -> None:
    """call_claude returns None when stdout is not valid JSON."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "not json at all"
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_call_claude_returns_none_on_oserror() -> None:
    """call_claude returns None when OSError is raised (claude not found)."""
    with patch("subprocess.run", side_effect=OSError("claude not found")):
        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_call_claude_returns_none_on_non_dict_response() -> None:
    """call_claude returns None when parsed JSON is not a dict (e.g. a list)."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "```json\n[1, 2, 3]\n```"
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


# ---------------------------------------------------------------------------
# DEFAULT_MODEL test
# ---------------------------------------------------------------------------


def test_default_model_is_correct() -> None:
    """DEFAULT_MODEL uses the stable claude-sonnet-4-5 alias."""
    assert DEFAULT_MODEL == "claude-sonnet-4-5"
