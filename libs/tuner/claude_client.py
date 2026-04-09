"""Claude CLI integration for the strategy parameter tuner.

Provides prompt construction, subprocess-based Claude CLI call, and structured
response parsing via shared JSON extraction utility.

Implements CLI-01: replaces Anthropic SDK call with subprocess.run(["claude", "-p", ...])
so the tuner container requires no ANTHROPIC_API_KEY and no SDK import.
"""

from __future__ import annotations

import subprocess
from typing import Any

import structlog

from libs.common.json_extractor import JsonExtractionError, extract_json
from libs.metrics.engine import StrategyMetrics
from libs.tuner.bounds import BoundsEntry

_logger = structlog.get_logger(__name__)

# Stable alias kept for backward compatibility with recommender.py which imports DEFAULT_MODEL.
# The value is unused by the CLI path — claude -p uses its own default model.
DEFAULT_MODEL: str = "claude-sonnet-4-5"

# Subprocess hard timeout (seconds). Prevents indefinite blocking (T-27-02).
_CLI_TIMEOUT_SECONDS: int = 120


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
        "5. Respond with a JSON code block as shown in the Output Format section below. "
        "Include a top-level summary of your overall assessment.\n\n"
        "Focus on: improving expectancy, reducing drawdown, and increasing win rate "
        "for strategies and instruments with sufficient data (>= 10 completed round-trips).\n\n"
        "## Output Format\n"
        "Respond with ONLY a JSON code block — no prose before or after.\n"
        "```json\n"
        "{\n"
        '  "summary": "string — overall assessment of portfolio performance",\n'
        '  "recommendations": [\n'
        "    {\n"
        '      "strategy": "string — strategy name",\n'
        '      "instrument": "string or null — instrument ID or null for base-level",\n'
        '      "param": "string — parameter name from bounds registry",\n'
        '      "value": "number — new value within bounds",\n'
        '      "reasoning": "string — why this change, referencing metrics"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "Return an empty recommendations array if no changes are warranted."
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
    """Call the Claude CLI and return the parsed JSON response dict.

    Invokes ``claude -p`` via subprocess.run(), passing the combined prompt as
    the positional argument. The ``model`` and ``max_tokens`` parameters are
    accepted for backward compatibility with recommender.py but are ignored —
    the Claude CLI uses its own default model.

    Args:
        model: Ignored. Accepted for interface compatibility with recommender.py.
        system_prompt: System prompt from build_system_prompt().
        user_message: User message from build_user_message().
        max_tokens: Ignored. Accepted for interface compatibility with recommender.py.

    Returns:
        Parsed dict with 'summary' and 'recommendations' keys, or None on any error.

    Raises:
        Never -- all errors are caught and returned as None.
    """
    full_prompt = f"{system_prompt}\n\n{user_message}"

    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt],
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _logger.error("tuner_claude_cli_timeout", timeout_seconds=_CLI_TIMEOUT_SECONDS)
        return None
    except OSError as e:
        _logger.error("tuner_claude_cli_not_found", error=str(e))
        return None

    if result.returncode != 0:
        _logger.error(
            "tuner_claude_cli_error",
            returncode=result.returncode,
            # Truncate stderr to avoid leaking large error output (T-27-03).
            stderr=result.stderr[:500] if result.stderr else "",
        )
        return None

    try:
        parsed = extract_json(result.stdout)
    except JsonExtractionError as e:
        _logger.error("tuner_claude_parse_error", error=str(e))
        return None

    # Only dict responses are valid — reject list output (T-27-01).
    if not isinstance(parsed, dict):
        _logger.warning("tuner_claude_unexpected_type", type=type(parsed).__name__)
        return None

    return parsed
