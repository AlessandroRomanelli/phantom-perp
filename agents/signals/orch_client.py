"""Claude orchestrator client for dynamic strategy enable/disable and parameter adjustment.

Handles tool schema definition, multi-instrument context assembly from FeatureStore
and MarketSnapshot data, async Anthropic API calls with forced tool use, and
response validation that clips parameter adjustments against bounds.yaml.

Key guarantees:
- All inference is async (AsyncAnthropic) — signals agent runs under asyncio.
- Parameter adjustments are hard-clipped against bounds.yaml before returning.
- Unknown parameters are rejected with a warning (not silently accepted).
- All errors are logged and return None — never propagate exceptions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
import structlog

from libs.tuner.bounds import clip_value, load_bounds_registry

if TYPE_CHECKING:
    from agents.alpha.regime_detector import RegimeDetector
    from agents.signals.feature_store import FeatureStore
    from libs.common.models.market_snapshot import MarketSnapshot

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Model alias
# ---------------------------------------------------------------------------

_MODEL: str = "claude-sonnet-4-20250514"

# Default bounds path — same relative pattern as load_strategy_matrix() in main.py
_DEFAULT_BOUNDS_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "bounds.yaml"
)

# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

ORCHESTRATOR_TOOL: dict[str, Any] = {
    "name": "submit_orchestrator_decisions",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "instrument": {"type": "string"},
                        "strategy": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "param_adjustments": {
                            "type": "object",
                            "additionalProperties": {"type": "number"},
                        },
                        "reasoning": {"type": "string"},
                    },
                    "required": ["instrument", "strategy", "enabled", "reasoning"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["decisions", "summary"],
    },
}


# ---------------------------------------------------------------------------
# Params dataclass
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorParams:
    """Tunable parameters for the LLM strategy orchestrator.

    Mutable (not frozen) — same pattern as ContrarianFundingParams.
    """

    enabled: bool = True
    update_interval_seconds: int = 14400  # 4 hours
    min_interval_seconds: int = 3600  # 1 hour cooldown
    max_tokens: int = 1024


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_orchestrator_system_prompt() -> str:
    """Return the system prompt for the strategy orchestrator role.

    Returns:
        System prompt string instructing Claude to act as a strategy orchestrator.
    """
    return (
        "You are a strategy orchestrator for a perpetual futures trading bot "
        "on Coinbase International Exchange. Your job is to evaluate current market "
        "conditions across multiple instruments and decide which trading strategies "
        "should be active, and suggest bounded parameter adjustments.\n\n"
        "Rules:\n"
        "1. Enable strategies that are well-suited to the current regime and conditions.\n"
        "2. Disable strategies that are likely to generate false signals in the current regime.\n"
        "3. Suggest param_adjustments only for known tunable parameters "
        "(min_conviction, portfolio_a_min_conviction, cooldown_bars, stop_loss_atr_mult, "
        "take_profit_atr_mult, weight, funding_rate_boost, adx_threshold).\n"
        "4. Keep param_adjustments minimal — only adjust when there is strong evidence "
        "from the market data.\n"
        "5. Reasoning must reference specific numbers from the context (regime, vol, funding).\n"
        "6. Be conservative: in HIGH_VOLATILITY regimes, prefer disabling aggressive strategies.\n"
        "7. Use the submit_orchestrator_decisions tool to return all your decisions.\n\n"
        "Output: one decision entry per (instrument, strategy) combination you wish to change."
    )


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def build_orchestrator_context(
    instrument_ids: list[str],
    slow_stores: dict[str, FeatureStore],
    latest_snapshots: dict[str, MarketSnapshot],
    regime_detector: RegimeDetector,
) -> str:
    """Assemble a condensed multi-instrument context string for the orchestrator.

    Produces a token-efficient summary (~600 chars) covering regime, volatility,
    funding rate trend, and OI trend for each instrument.

    Args:
        instrument_ids: List of instrument IDs to include (e.g. ['ETH-PERP', 'BTC-PERP']).
        slow_stores: Dict of FeatureStore instances keyed by instrument ID.
        latest_snapshots: Dict of most recent MarketSnapshot per instrument.
        regime_detector: RegimeDetector with per-instrument regime state.

    Returns:
        Multi-line context string suitable for inclusion in a user message to Claude.
    """
    from agents.signals.main import STRATEGY_CLASSES  # local import to avoid circulars

    lines: list[str] = []
    lines.append("## Orchestrator Context")
    lines.append(f"Instruments: {', '.join(instrument_ids)}")
    lines.append("")

    for instr in instrument_ids:
        # Regime
        regime_val = regime_detector.regime_for(instr).value

        # Volatility from snapshot (None-safe)
        snapshot = latest_snapshots.get(instr)
        vol_1h = f"{snapshot.volatility_1h:.4f}" if snapshot is not None else "N/A"

        # Funding rate + direction from feature store
        store = slow_stores.get(instr)
        funding_str = "N/A"
        oi_str = "N/A"

        if store is not None:
            funding_arr = store.funding_rates
            if len(funding_arr) >= 2:
                tail_f = funding_arr[-10:]
                last_f = tail_f[-1]
                direction_f = "rising" if tail_f[-1] > tail_f[0] else (
                    "falling" if tail_f[-1] < tail_f[0] else "flat"
                )
                funding_str = f"{last_f:+.6f} ({direction_f})"
            elif len(funding_arr) == 1:
                funding_str = f"{funding_arr[0]:+.6f} (flat)"
            elif snapshot is not None:
                funding_str = f"{float(snapshot.funding_rate):+.6f} (flat)"

            # OI + pct change
            oi_arr = store.open_interests
            if len(oi_arr) >= 2:
                tail_oi = oi_arr[-10:]
                oi_first = tail_oi[0]
                oi_last = tail_oi[-1]
                pct = ((oi_last - oi_first) / oi_first * 100) if oi_first != 0.0 else 0.0
                oi_str = f"{oi_last:.0f} ({pct:+.1f}%)"
            elif len(oi_arr) == 1:
                oi_str = f"{oi_arr[0]:.0f} (+0.0%)"

        lines.append(
            f"{instr} | regime={regime_val} | vol_1h={vol_1h} "
            f"| funding={funding_str} | OI={oi_str}"
        )

    lines.append("")
    strategy_names = ", ".join(STRATEGY_CLASSES.keys())
    lines.append(f"Active strategies: {strategy_names}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Async API call
# ---------------------------------------------------------------------------


async def call_claude_orchestrator(
    context_str: str,
    params: OrchestratorParams,
) -> list[dict[str, Any]] | None:
    """Call Claude API asynchronously for strategy orchestration decisions.

    Uses ``anthropic.AsyncAnthropic()`` with forced ``submit_orchestrator_decisions``
    tool use.  Returns the raw decisions list from Claude's tool input.

    Args:
        context_str: Multi-instrument context string from build_orchestrator_context().
        params: OrchestratorParams controlling model behaviour (max_tokens).

    Returns:
        List of raw decision dicts from Claude's tool input, or None if the API
        key is missing, an API error occurs, or no tool_use block is found.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _logger.error("orchestrator_api_key_missing")
        return None

    system_prompt = _build_orchestrator_system_prompt()
    user_message = (
        "Evaluate the following multi-instrument market context and decide which "
        "strategies should be enabled or disabled, and whether any parameter "
        f"adjustments are warranted.\n\n{context_str}"
    )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(  # type: ignore[call-overload]
            model=_MODEL,
            max_tokens=params.max_tokens,
            system=system_prompt,
            tools=[ORCHESTRATOR_TOOL],
            tool_choice={"type": "tool", "name": "submit_orchestrator_decisions"},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        _logger.error(
            "orchestrator_api_error",
            error=str(exc),
            status_code=getattr(exc, "status_code", None),
        )
        return None

    # Extract tool_use block — forced tool_choice guarantees its presence
    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_use_block is None:
        _logger.warning("orchestrator_no_tool_use_block")
        return None

    raw: dict[str, Any] = tool_use_block.input
    decisions: list[dict[str, Any]] = raw.get("decisions", [])

    _logger.info(
        "orchestrator_decisions_received",
        num_decisions=len(decisions),
        summary=raw.get("summary", "")[:120],
    )

    return decisions


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def validate_orchestrator_response(
    decisions: list[dict[str, Any]],
    bounds_path: Path = _DEFAULT_BOUNDS_PATH,
) -> list[dict[str, Any]]:
    """Validate and clip orchestrator decisions against the bounds registry.

    For each decision:
    - Preserves instrument, strategy, enabled, reasoning.
    - Skips param_adjustments whose key is not in the bounds registry (with warning).
    - Clips param_adjustments values to [min, max] from the registry (with warning if clipped).

    Args:
        decisions: Raw decision dicts from call_claude_orchestrator().
        bounds_path: Path to bounds.yaml registry file.

    Returns:
        List of validated decision dicts with clipped param_adjustments.
    """
    try:
        registry = load_bounds_registry(bounds_path)
    except FileNotFoundError:
        _logger.error(
            "orchestrator_bounds_not_found",
            path=str(bounds_path),
        )
        # Return decisions with empty param_adjustments as a safe fallback
        return [
            {
                "instrument": d.get("instrument", ""),
                "strategy": d.get("strategy", ""),
                "enabled": bool(d.get("enabled", True)),
                "reasoning": d.get("reasoning", ""),
                "param_adjustments": {},
            }
            for d in decisions
        ]

    validated: list[dict[str, Any]] = []

    for decision in decisions:
        instrument = decision.get("instrument", "")
        strategy = decision.get("strategy", "")
        enabled = bool(decision.get("enabled", True))
        reasoning = str(decision.get("reasoning", ""))
        raw_adjustments: dict[str, Any] = decision.get("param_adjustments") or {}

        clipped_adjustments: dict[str, float] = {}

        for param_name, raw_value in raw_adjustments.items():
            if param_name not in registry:
                _logger.warning(
                    "orchestrator_unknown_param",
                    param=param_name,
                    instrument=instrument,
                    strategy=strategy,
                )
                continue  # reject unknown param

            proposed = float(raw_value)
            clipped = clip_value(param_name, proposed, registry)

            if clipped != proposed:
                _logger.warning(
                    "orchestrator_param_clipped",
                    param=param_name,
                    proposed=proposed,
                    clipped=clipped,
                    instrument=instrument,
                    strategy=strategy,
                )

            clipped_adjustments[param_name] = clipped

        validated.append(
            {
                "instrument": instrument,
                "strategy": strategy,
                "enabled": enabled,
                "reasoning": reasoning,
                "param_adjustments": clipped_adjustments,
            }
        )

    return validated
