"""Claude CLI orchestrator client for dynamic strategy enable/disable and parameter adjustment.

Handles multi-instrument context assembly from FeatureStore and MarketSnapshot data,
async subprocess calls to the Claude CLI with a configurable timeout, and response
validation that clips parameter adjustments against bounds.yaml.

Key guarantees:
- All inference is async (asyncio.create_subprocess_exec) — signals agent runs under asyncio.
- Parameter adjustments are hard-clipped against bounds.yaml before returning.
- Unknown parameters are rejected with a warning (not silently accepted).
- All errors are logged and return None — never propagate exceptions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from libs.common.json_extractor import JsonExtractionError, extract_json
from libs.tuner.bounds import clip_value, load_bounds_registry

if TYPE_CHECKING:
    from agents.alpha.regime_detector import RegimeDetector
    from agents.signals.feature_store import FeatureStore
    from agents.signals.news_client import CryptoHeadline, EconomicEvent
    from libs.common.models.market_snapshot import MarketSnapshot

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# CLI timeout
# ---------------------------------------------------------------------------

# Orchestrator context is larger than market analysis — allow more time.
_CLI_TIMEOUT_SECONDS: int = 120

# Default bounds path — same relative pattern as load_strategy_matrix() in main.py
_DEFAULT_BOUNDS_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "bounds.yaml"
)


# ---------------------------------------------------------------------------
# Params dataclass
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorParams:
    """Tunable parameters for the LLM strategy orchestrator.

    Mutable (not frozen) — same pattern as ContrarianFundingParams.
    """

    enabled: bool = True
    update_interval_seconds: int = 7200  # 2 hours
    min_interval_seconds: int = 3600  # 1 hour cooldown
    max_tokens: int = 1024
    min_confidence_threshold: float = 0.7


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
        "(min_conviction, route_a_min_conviction, cooldown_bars, stop_loss_atr_mult, "
        "take_profit_atr_mult, weight, funding_rate_boost, adx_threshold).\n"
        "4. Keep param_adjustments minimal — only adjust when there is strong evidence "
        "from the market data.\n"
        "5. Reasoning must reference specific numbers from the context (regime, vol, funding).\n"
        "6. Be conservative: in HIGH_VOLATILITY regimes, prefer disabling aggressive strategies.\n"
        "7. Include a confidence field (0.0–1.0) for each decision: 1.0 = very confident, "
        "0.0 = highly uncertain.\n"
        "8. Respond with a JSON code block as shown in the Output Format section below.\n"
        "9. If high-impact macro events (FOMC, CPI, NFP) are scheduled within 24h, "
        "prefer disabling momentum and breakout strategies to avoid false breakouts.\n"
        "10. If crypto headlines contain tail-risk keywords (hack, exploit, regulatory, "
        "SEC, ban), reduce enabled strategies and lower conviction thresholds.\n\n"
        "Output: one decision entry per (instrument, strategy) combination you wish to change.\n\n"
        "## Output Format\n"
        "Respond with ONLY a JSON code block — no prose before or after.\n"
        "```json\n"
        "{\n"
        '  "decisions": [\n'
        "    {\n"
        '      "instrument": "string — instrument ID",\n'
        '      "strategy": "string — strategy name",\n'
        '      "enabled": "boolean — whether to enable this strategy",\n'
        '      "param_adjustments": {"param_name": "number"},\n'
        '      "reasoning": "string — referencing specific market data",\n'
        '      "confidence": "number — 0.0 to 1.0"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "string — overall orchestration assessment"\n'
        "}\n"
        "```"
    )


# ---------------------------------------------------------------------------
# News context formatter
# ---------------------------------------------------------------------------


def _format_news_context(
    headlines: list[CryptoHeadline],
    events: list[EconomicEvent],
) -> str:
    """Render a ## News Context block for inclusion in the orchestrator context.

    Limits headlines to the 5 most recent. Lists all provided events.

    Args:
        headlines: List of CryptoHeadline objects (may be empty).
        events: List of EconomicEvent objects for the next 48h (may be empty).

    Returns:
        Formatted multi-line string starting with ``## News Context``.
    """
    lines: list[str] = ["## News Context"]

    # Headlines subsection
    recent = headlines[:5]
    if recent:
        lines.append(f"Headlines ({len(recent)} most recent):")
        for h in recent:
            currencies_str = ", ".join(h.currencies) if h.currencies else "—"
            lines.append(f"- {h.title} ({h.source}, {currencies_str})")
    else:
        lines.append("Headlines: none available.")

    # Economic events subsection
    if events:
        lines.append("Upcoming High-Impact Events (next 48h):")
        for e in events:
            time_str = e.event_time.strftime("%Y-%m-%d")
            est = e.estimate if e.estimate is not None else "N/A"
            prev = e.previous if e.previous is not None else "N/A"
            lines.append(f"- {e.event} @ {time_str} (est: {est}, prev: {prev})")
    else:
        lines.append("Upcoming High-Impact Events: none in next 48h.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def build_orchestrator_context(
    instrument_ids: list[str],
    slow_stores: dict[str, FeatureStore],
    latest_snapshots: dict[str, MarketSnapshot],
    regime_detector: RegimeDetector,
    headlines: list[CryptoHeadline] | None = None,
    events: list[EconomicEvent] | None = None,
) -> str:
    """Assemble a condensed multi-instrument context string for the orchestrator.

    Produces a token-efficient summary (~600 chars) covering regime, volatility,
    funding rate trend, and OI trend for each instrument. Optionally appends a
    ``## News Context`` section with recent crypto headlines and upcoming high-impact
    economic events.

    Args:
        instrument_ids: List of instrument IDs to include (e.g. ['ETH-PERP', 'BTC-PERP']).
        slow_stores: Dict of FeatureStore instances keyed by instrument ID.
        latest_snapshots: Dict of most recent MarketSnapshot per instrument.
        regime_detector: RegimeDetector with per-instrument regime state.
        headlines: Optional list of recent CryptoHeadline objects.
        events: Optional list of upcoming EconomicEvent objects.

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

    lines.append("")
    lines.append(_format_news_context(headlines or [], events or []))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Async CLI call
# ---------------------------------------------------------------------------


async def call_claude_orchestrator(
    context_str: str,
    params: OrchestratorParams,
) -> list[dict[str, Any]] | None:
    """Call the Claude CLI asynchronously for strategy orchestration decisions.

    Uses ``asyncio.create_subprocess_exec("claude", "-p", ...)`` with a
    ``_CLI_TIMEOUT_SECONDS`` hard cap to prevent event loop stalls.
    Parses CLI stdout via ``extract_json()`` and extracts the ``decisions``
    list from the top-level JSON object.

    Args:
        context_str: Multi-instrument context string from build_orchestrator_context().
        params: OrchestratorParams controlling model behaviour (unused by CLI directly;
            kept for interface compatibility with callers).

    Returns:
        List of raw decision dicts from Claude's JSON output, or None if a
        subprocess/parsing error occurs.
    """
    system_prompt = _build_orchestrator_system_prompt()
    user_message = (
        "Evaluate the following multi-instrument market context and decide which "
        "strategies should be enabled or disabled, and whether any parameter "
        f"adjustments are warranted.\n\n{context_str}"
    )

    full_prompt = f"{system_prompt}\n\n{user_message}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=_CLI_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        _logger.error(
            "orchestrator_cli_timeout",
            timeout_seconds=_CLI_TIMEOUT_SECONDS,
        )
        return None
    except OSError as e:
        _logger.error(
            "orchestrator_cli_not_found",
            error=str(e),
        )
        return None

    if proc.returncode != 0:
        _logger.error(
            "orchestrator_cli_error",
            returncode=proc.returncode,
            stderr=(stderr_bytes or b"").decode()[:500],
        )
        return None

    stdout_text = (stdout_bytes or b"").decode()

    try:
        parsed = extract_json(stdout_text)
    except JsonExtractionError as e:
        _logger.error(
            "orchestrator_parse_error",
            error=str(e),
        )
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "orchestrator_unexpected_response_type",
            type=type(parsed).__name__,
        )
        return None

    decisions: list[dict[str, Any]] = parsed.get("decisions", [])

    _logger.info(
        "orchestrator_decisions_received",
        num_decisions=len(decisions),
        summary=parsed.get("summary", "")[:120],
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
                "confidence": float(d.get("confidence", 1.0)),
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
                "confidence": float(decision.get("confidence", 1.0)),
            }
        )

    return validated
