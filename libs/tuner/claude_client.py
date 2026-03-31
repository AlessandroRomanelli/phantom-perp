"""Claude API integration for the strategy parameter tuner.

Provides prompt construction, Anthropic SDK call with forced tool use,
and structured response parsing. This module is the intelligence interface:
it sends performance data to Claude and receives typed parameter recommendations.

Implements CLAI-01 (SDK call with structured output), CLAI-02 (typed JSON with
per-parameter reasoning), and CLAI-03 (prompt includes params, bounds, metrics).
"""

from __future__ import annotations

from typing import Any

import anthropic
import structlog

from libs.metrics.engine import StrategyMetrics
from libs.tuner.bounds import BoundsEntry

_logger = structlog.get_logger(__name__)

# Stable alias -- resolves to claude-sonnet-4-5-20250929
# Never use the invalid date-stamped ID claude-sonnet-4-5-20250514
DEFAULT_MODEL: str = "claude-sonnet-4-5"

# Tool schema enforcing structured recommendation output (CLAI-02).
# strict=True guarantees all required fields are present in the response.
TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_recommendations",
    "description": (
        "Submit parameter tuning recommendations after analyzing strategy performance. "
        "Return an empty recommendations list if no changes are warranted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "Overall assessment of portfolio performance and key observations. "
                    "Summarize what is working, what is not, and the overall direction of changes."
                ),
            },
            "recommendations": {
                "type": "array",
                "description": "List of parameter change recommendations. May be empty if no changes are warranted.",
                "items": {
                    "type": "object",
                    "properties": {
                        "strategy": {
                            "type": "string",
                            "description": "Strategy name (e.g. 'momentum', 'mean_reversion').",
                        },
                        "instrument": {
                            "type": ["string", "null"],
                            "description": (
                                "Instrument ID (e.g. 'ETH-PERP') or null for base-level params "
                                "that apply across all instruments."
                            ),
                        },
                        "param": {
                            "type": "string",
                            "description": "Parameter name matching a key in the bounds registry.",
                        },
                        "value": {
                            "type": "number",
                            "description": "Recommended new value. Must be within the registered min/max bounds.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": (
                                "Explanation of why this change is recommended, "
                                "referencing specific performance data that supports the decision."
                            ),
                        },
                    },
                    "required": ["strategy", "instrument", "param", "value", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "recommendations"],
        "additionalProperties": False,
    },
    "strict": True,
}


def build_system_prompt() -> str:
    """Build the system prompt defining Claude's role and rules (D-01).

    Returns:
        System prompt string for the parameter tuner role.
    """
    return (
        "You are a trading strategy parameter tuner for Portfolio A of a perpetual futures "
        "trading bot on Coinbase International Exchange.\n\n"
        "Your task is to analyze recent strategy performance metrics and recommend parameter "
        "adjustments that will improve Portfolio A's risk-adjusted returns.\n\n"
        "Rules:\n"
        "1. Only adjust parameters that appear in the bounds registry. Do not invent new parameters.\n"
        "2. All recommended values MUST respect the min/max bounds listed in the registry. "
        "Code will clip any out-of-bounds value, but you should stay within bounds to signal "
        "intentional recommendations.\n"
        "3. Provide clear reasoning for each recommendation, referencing the specific metrics "
        "that support the change.\n"
        "4. If no changes are warranted (e.g., insufficient data or already well-tuned), "
        "return an empty recommendations list with a summary explaining why.\n"
        "5. Use the submit_recommendations tool to return your analysis. "
        "Include a top-level summary of your overall assessment.\n\n"
        "Focus on: improving expectancy, reducing drawdown, and increasing win rate "
        "for strategies and instruments with sufficient data (>= 10 completed round-trips)."
    )


def build_user_message(
    metrics: dict[tuple[str, str], StrategyMetrics | None],
    current_params: dict[str, dict[str, Any]],
    registry: dict[str, BoundsEntry],
) -> str:
    """Build the structured user message with all 4 required sections (D-02, CLAI-03).

    Sections:
    1. Performance Metrics -- per (strategy, instrument) with None shown as insufficient data
    2. Current Parameter Values -- current YAML values for each strategy
    3. Bounds Registry -- hard limits per tunable parameter
    4. Tunable Parameters -- list of tunable param names with brief descriptions

    Args:
        metrics: Output from compute_strategy_metrics(). None values = below min-trade gate.
        current_params: Strategy name -> config dict from load_strategy_config().
        registry: Bounds registry from load_bounds_registry().

    Returns:
        Formatted user message string.
    """
    sections: list[str] = []

    # --- Section 1: Performance Metrics ---
    sections.append(_build_metrics_section(metrics))

    # --- Section 2: Current Parameter Values ---
    sections.append(_build_params_section(current_params))

    # --- Section 3: Bounds Registry ---
    sections.append(_build_bounds_section(registry))

    # --- Section 4: Tunable Parameters ---
    sections.append(_build_tunable_params_section(registry))

    return "\n\n".join(sections)


def _build_metrics_section(
    metrics: dict[tuple[str, str], StrategyMetrics | None],
) -> str:
    """Build the Performance Metrics markdown section."""
    lines = ["## Performance Metrics (last 30 days)"]
    lines.append(
        "| strategy | instrument | trades | win_rate | expectancy_usdc | "
        "profit_factor | net_pnl_usdc | max_drawdown_usdc |"
    )
    lines.append("|----------|-----------|--------|----------|-----------------|"
                 "---------------|--------------|-------------------|")

    for (source, instrument), m in sorted(metrics.items()):
        if m is None:
            lines.append(
                f"| {source} | {instrument} | — | — | "
                f"insufficient data (< 10 trades) | — | — | — |"
            )
        else:
            pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "N/A"
            lines.append(
                f"| {source} | {instrument} | {m.trade_count} | {m.win_rate:.2%} | "
                f"{m.expectancy_usdc:.2f} | {pf} | "
                f"{m.total_net_pnl:.2f} | {m.max_drawdown_usdc:.2f} |"
            )

    return "\n".join(lines)


def _build_params_section(current_params: dict[str, dict[str, Any]]) -> str:
    """Build the Current Parameter Values markdown section."""
    lines = ["## Current Parameter Values"]

    for strategy_name, config in sorted(current_params.items()):
        lines.append(f"\nstrategy: {strategy_name}")

        # Base parameters
        base_params = config.get("parameters", {})
        for param_key, param_val in sorted(base_params.items()):
            lines.append(f"  parameters.{param_key}: {param_val}")

        # Per-instrument overrides
        instruments = config.get("instruments", {})
        for instrument_id, inst_config in sorted(instruments.items()):
            inst_params = inst_config.get("parameters", {})
            for param_key, param_val in sorted(inst_params.items()):
                lines.append(
                    f"  instruments.{instrument_id}.parameters.{param_key}: {param_val}"
                )

    return "\n".join(lines)


def _build_bounds_section(registry: dict[str, BoundsEntry]) -> str:
    """Build the Bounds Registry markdown section (hard limits)."""
    lines = [
        "## Bounds Registry (hard limits — never exceed these)",
        "| param | min | max | type |",
        "|-------|-----|-----|------|",
    ]
    for param_name, entry in sorted(registry.items()):
        # Format as fixed decimal to avoid 0.1 vs 0.10 ambiguity
        min_str = f"{entry.min_value:.2f}" if entry.value_type == "float" else str(int(entry.min_value))
        max_str = f"{entry.max_value:.2f}" if entry.value_type == "float" else str(int(entry.max_value))
        lines.append(
            f"| {param_name} | {min_str} | {max_str} | {entry.value_type} |"
        )
    return "\n".join(lines)


def _build_tunable_params_section(registry: dict[str, BoundsEntry]) -> str:
    """Build the Tunable Parameters section listing all adjustable param names."""
    param_descriptions: dict[str, str] = {
        "min_conviction": "Minimum conviction threshold to emit a signal",
        "route_a_min_conviction": "Minimum conviction for Route A autonomous execution",
        "cooldown_bars": "Number of bars to wait after a signal before generating a new one",
        "stop_loss_atr_mult": "ATR multiplier for stop-loss placement",
        "take_profit_atr_mult": "ATR multiplier for take-profit placement",
        "weight": "Strategy weight in alpha combiner (relative to other strategies)",
        "funding_rate_boost": "Conviction boost when funding rate aligns with signal direction",
        "adx_threshold": "ADX threshold for regime trend strength qualification",
    }

    lines = ["## Tunable Parameters"]
    lines.append("The following parameters may be adjusted (must match bounds registry keys):\n")
    for param_name in sorted(registry.keys()):
        description = param_descriptions.get(param_name, "Tunable strategy parameter")
        lines.append(f"- **{param_name}**: {description}")

    return "\n".join(lines)


def call_claude(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4096,
) -> dict[str, Any] | None:
    """Call Claude API with forced tool use and return the tool input dict.

    Uses the sync anthropic.Anthropic() client (appropriate for a run-to-completion
    tuner container -- no event loop needed). Forces the submit_recommendations tool
    call via tool_choice, guaranteeing a structured response.

    Args:
        model: Anthropic model ID (e.g. DEFAULT_MODEL).
        system_prompt: System prompt from build_system_prompt().
        user_message: User message from build_user_message().
        max_tokens: Maximum output tokens (default 4096 -- generous for 35 strategy/instrument pairs).

    Returns:
        Tool input dict with 'summary' and 'recommendations' keys, or None on any error.

    Raises:
        Never -- all errors are caught and returned as None per D-13/D-14.
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "submit_recommendations"},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        _logger.error(
            "tuner_claude_api_error",
            error=str(e),
            status_code=getattr(e, "status_code", None),
        )
        return None

    # With forced tool_choice, response should always contain a tool_use block.
    # Guard defensively anyway -- strict mode is a best-effort guarantee.
    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        _logger.warning(
            "tuner_parse_error",
            reason="no tool_use block in response despite forced tool_choice",
        )
        return None

    # tool_use_block.input is already a Python dict -- no json.loads() needed
    return tool_use_block.input  # type: ignore[no-any-return]
