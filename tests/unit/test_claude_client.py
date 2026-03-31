"""Unit tests for libs/tuner/claude_client.py.

Coverage:
- build_system_prompt: returns string with role and safety constraint references
- build_user_message: contains all 4 required sections (CLAI-03)
- build_user_message: insufficient data entries for None metrics
- TOOL_SCHEMA: has required fields, strict mode enabled
- call_claude: extracts tool input dict from success response
- call_claude: returns None on anthropic.APIError (D-13)
- call_claude: returns None when no tool_use block in response (D-14)
- call_claude: passes correct model, system prompt, and max_tokens
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from libs.metrics.engine import StrategyMetrics
from libs.tuner.bounds import BoundsEntry
from libs.tuner.claude_client import (
    DEFAULT_MODEL,
    TOOL_SCHEMA,
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


def _make_fake_response(
    recommendations: list[dict],
    summary: str = "test summary",
) -> MagicMock:
    """Build a fake anthropic Message response with a tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"summary": summary, "recommendations": recommendations}
    msg = MagicMock()
    msg.content = [tool_block]
    return msg


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
# TOOL_SCHEMA tests
# ---------------------------------------------------------------------------


def test_tool_schema_name() -> None:
    """TOOL_SCHEMA has the correct tool name."""
    assert TOOL_SCHEMA["name"] == "submit_recommendations"


def test_tool_schema_has_required_root_fields() -> None:
    """TOOL_SCHEMA input_schema requires 'summary' and 'recommendations'."""
    required = TOOL_SCHEMA["input_schema"]["required"]
    assert "summary" in required
    assert "recommendations" in required


def test_tool_schema_recommendation_item_required_fields() -> None:
    """Each recommendation item requires strategy, param, value, reasoning."""
    items_schema = TOOL_SCHEMA["input_schema"]["properties"]["recommendations"]["items"]
    required = items_schema["required"]
    assert "strategy" in required
    assert "param" in required
    assert "value" in required
    assert "reasoning" in required


def test_tool_schema_strict_mode() -> None:
    """TOOL_SCHEMA has strict=True for schema enforcement (CLAI-02)."""
    assert TOOL_SCHEMA.get("strict") is True


# ---------------------------------------------------------------------------
# call_claude tests
# ---------------------------------------------------------------------------


def test_call_claude_returns_tool_input_on_success() -> None:
    """call_claude returns the tool input dict on a successful response."""
    recs = [{"strategy": "momentum", "instrument": "ETH-PERP", "param": "min_conviction",
             "value": 0.6, "reasoning": "test"}]
    fake_response = _make_fake_response(recs, summary="good session")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = fake_response

        result = call_claude(DEFAULT_MODEL, "system prompt", "user message")

    assert result is not None
    assert result["summary"] == "good session"
    assert result["recommendations"] == recs


def test_call_claude_uses_correct_model_and_tool_choice() -> None:
    """call_claude passes correct model, tool_choice, and tools to SDK."""
    fake_response = _make_fake_response([])

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = fake_response

        call_claude("claude-sonnet-4-5", "sys", "user")

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == "claude-sonnet-4-5"
    assert call_kwargs.kwargs["tools"] == [TOOL_SCHEMA]
    assert call_kwargs.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "submit_recommendations",
    }


def test_call_claude_passes_max_tokens_and_system() -> None:
    """call_claude passes system prompt and max_tokens to SDK."""
    fake_response = _make_fake_response([])

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = fake_response

        call_claude("claude-sonnet-4-5", "my system prompt", "user msg", max_tokens=4096)

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["system"] == "my system prompt"
    assert call_kwargs.kwargs["max_tokens"] == 4096


def test_call_claude_returns_none_on_api_error() -> None:
    """call_claude returns None when anthropic.APIError is raised (D-13)."""
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic.APIStatusError(
            message="Internal server error",
            response=MagicMock(status_code=500),
            body=None,
        )

        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_call_claude_returns_none_on_no_tool_block() -> None:
    """call_claude returns None when response has no tool_use block (D-14)."""
    msg = MagicMock()
    msg.content = []  # Empty content -- no tool_use block

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = msg

        result = call_claude(DEFAULT_MODEL, "sys", "user")

    assert result is None


def test_default_model_is_correct() -> None:
    """DEFAULT_MODEL uses the stable claude-sonnet-4-5 alias."""
    assert DEFAULT_MODEL == "claude-sonnet-4-5"
