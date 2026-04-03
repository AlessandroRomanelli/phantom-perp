"""Claude API integration for market analysis signals.

Handles tool schema definition, context assembly from FeatureStore and
MarketSnapshot data, async Anthropic API calls with forced tool use, and
structured response validation before signals reach the pipeline.

Key guarantees:
- All inference is async (AsyncAnthropic) — signals agent runs under asyncio.
- Response validation rejects bad prices, clamps conviction, and computes
  ATR-based defaults when Claude omits entry/stop/TP prices.
- NO_SIGNAL responses are silently converted to None (not an error).
- All errors are logged and return None — never propagate exceptions.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import anthropic
import numpy as np
import structlog

from agents.signals.feature_store import FeatureStore  # noqa: TC001
from libs.common.instruments import get_instrument
from libs.common.models.enums import MarketRegime, PositionSide
from libs.common.models.market_snapshot import MarketSnapshot  # noqa: TC001
from libs.indicators.volatility import atr

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Model alias
# ---------------------------------------------------------------------------

# Stable alias — same as libs/tuner/claude_client.py
_MODEL: str = "claude-sonnet-4-5"

# Maximum output tokens for market analysis (compact JSON response)
_MAX_TOKENS: int = 512

# Entry price tolerance: reject if more than ±5% from mark_price
_ENTRY_PRICE_MAX_DEVIATION = Decimal("0.05")

# ATR multipliers used when Claude omits stop/TP prices
_DEFAULT_STOP_ATR_MULT: float = 2.0
_DEFAULT_TP_ATR_MULT: float = 3.0
_ATR_PERIOD: int = 14

# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

MARKET_ANALYSIS_TOOL: dict[str, Any] = {
    "name": "submit_market_analysis",
    "description": (
        "Submit your market analysis result for a perpetual futures instrument. "
        "Use direction=NO_SIGNAL when there is no clear trade opportunity."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "instrument": {
                "type": "string",
                "description": "Instrument ID being analysed, e.g. 'ETH-PERP'.",
            },
            "direction": {
                "type": "string",
                "enum": ["LONG", "SHORT", "NO_SIGNAL"],
                "description": (
                    "Trade direction: LONG, SHORT, or NO_SIGNAL when no opportunity exists."
                ),
            },
            "conviction": {
                "type": "number",
                "description": (
                    "Signal conviction in [0, 1]. Higher values indicate higher confidence. "
                    "Must be ≥ 0.50 to be acted upon."
                ),
            },
            "entry_price": {
                "type": ["number", "null"],
                "description": (
                    "Suggested entry price in USDC. Must be within ±5%% of current mark price. "
                    "Pass null to use ATR-based default."
                ),
            },
            "stop_loss": {
                "type": ["number", "null"],
                "description": (
                    "Stop-loss price in USDC. For LONG: below entry. For SHORT: above entry. "
                    "Pass null to use ATR-based default."
                ),
            },
            "take_profit": {
                "type": ["number", "null"],
                "description": (
                    "Take-profit price in USDC. For LONG: above entry. For SHORT: below entry. "
                    "Pass null to use ATR-based default."
                ),
            },
            "time_horizon_hours": {
                "type": "number",
                "description": "Expected holding period in hours (e.g. 1, 4, 24).",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Concise explanation of the trade thesis. Reference specific "
                    "data points from the context (price, funding, OI, regime)."
                ),
            },
        },
        "required": [
            "instrument",
            "direction",
            "conviction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "time_horizon_hours",
            "reasoning",
        ],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    """Build the system prompt defining Claude's market analyst role.

    Returns:
        System prompt string for the market analysis role.
    """
    return (
        "You are a quantitative market analyst for a perpetual futures trading bot "
        "on Coinbase International Exchange. Your job is to analyse the provided market "
        "context for a single instrument and decide whether there is a high-confidence "
        "trade opportunity.\n\n"
        "You receive multi-timeframe data:\n"
        "- Intraday: 8 hours of 5-minute bars (microstructure, momentum)\n"
        "- Hourly candles: up to 24 hours (daily trend, support/resistance)\n"
        "- 6-hour candles: up to 7 days (macro trend, weekly structure)\n"
        "- Funding rate and OI trends: 2 hours of history\n"
        "- Live orderbook state and volatility\n\n"
        "Rules:\n"
        "1. Only emit LONG or SHORT when conviction ≥ 0.55. Otherwise use NO_SIGNAL.\n"
        "2. Look for alignment across timeframes — intraday move confirmed by hourly/daily trend.\n"
        "3. Entry price must be within ±5% of the current mark price.\n"
        "4. Stop-loss must be directionally correct: below entry for LONG, above for SHORT.\n"
        "5. Take-profit must be directionally correct: above entry for LONG, below for SHORT.\n"
        "6. Reasoning must reference specific numbers from the context provided.\n"
        "7. Be conservative in HIGH_VOLATILITY regimes — widen stops or use NO_SIGNAL.\n"
        "8. Funding rate alignment is a strong confirming factor — note it explicitly.\n"
        "9. Use the submit_market_analysis tool to return your analysis.\n\n"
        "Output style: be concise — two or three sentences of reasoning referencing "
        "specific price levels, timeframe alignment, and key indicators."
    )


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def build_market_context(
    instrument_id: str,
    store: FeatureStore,
    snapshot: MarketSnapshot,
    regime: MarketRegime,
) -> str:
    """Assemble a compact market context string for Claude.

    Summarises the most recent price action, funding rate trend, open
    interest trend, and current market state.  Token-efficient by design:
    uses concise labels and only shows last-N values for trend series.

    Args:
        instrument_id: Instrument identifier (e.g. 'ETH-PERP').
        store: Feature store with rolling price/funding/OI history.
        snapshot: Current market snapshot for live price/volatility data.
        regime: Currently detected market regime.

    Returns:
        Multi-line text summary suitable for inclusion in a user message.
    """
    lines: list[str] = []
    lines.append(f"## Market Context: {instrument_id}")
    lines.append(f"Timestamp: {snapshot.timestamp.isoformat()}")
    lines.append(f"Regime: {regime.value}")
    lines.append("")

    # --- Raw arrays from feature store (5-min bars) ---
    closes = store.closes
    highs = store.highs
    lows = store.lows
    volumes = store.volumes
    n = len(closes)

    mark = float(snapshot.mark_price)

    # --- Intraday price (last 96 samples = 8 hours of 5-min bars) ---
    window_96 = closes[-96:] if n >= 96 else closes
    lines.append("### Intraday Price (last 8 hours, 5-min bars)")
    if len(window_96) >= 2:
        lines.append(f"  bars={len(window_96)}  min={window_96.min():.2f}  max={window_96.max():.2f}")
        lines.append(f"  mean={window_96.mean():.2f}  first={window_96[0]:.2f}  last={window_96[-1]:.2f}")
        pct_8h = (window_96[-1] - window_96[0]) / window_96[0] * 100 if window_96[0] != 0 else 0
        lines.append(f"  change={pct_8h:+.2f}%")
    lines.append(f"  mark_price={mark:.2f}  total_samples_stored={n}")
    lines.append("")

    # --- Hourly candles synthesized from 5-min bars (last 24 hours) ---
    # Each hourly candle = 12 consecutive 5-min bars
    hourly_bars = 12
    max_hourly = min(n // hourly_bars, 24)
    if max_hourly >= 2:
        lines.append(f"### Hourly Candles (last {max_hourly} hours, synthesized from 5-min bars)")
        lines.append("  hour_ago | open | high | low | close | range%")
        for i in range(max_hourly, 0, -1):
            end_idx = n - (i - 1) * hourly_bars
            start_idx = end_idx - hourly_bars
            c_slice = closes[start_idx:end_idx]
            h_slice = highs[start_idx:end_idx]
            l_slice = lows[start_idx:end_idx]
            o = c_slice[0]
            c = c_slice[-1]
            h = h_slice.max()
            lo = l_slice.min()
            rng = (h - lo) / lo * 100 if lo > 0 else 0
            lines.append(f"  -{i:>2}h | {o:.2f} | {h:.2f} | {lo:.2f} | {c:.2f} | {rng:.2f}%")
        lines.append("")

    # --- 6-hour candles synthesized from 5-min bars (last 7 days) ---
    six_hour_bars = 72  # 6h × 12 bars/h
    max_6h = min(n // six_hour_bars, 28)  # up to 7 days
    if max_6h >= 2:
        lines.append(f"### 6-Hour Candles (last {max_6h} periods, synthesized)")
        lines.append("  periods_ago | open | high | low | close | change%")
        for i in range(max_6h, 0, -1):
            end_idx = n - (i - 1) * six_hour_bars
            start_idx = end_idx - six_hour_bars
            c_slice = closes[start_idx:end_idx]
            h_slice = highs[start_idx:end_idx]
            l_slice = lows[start_idx:end_idx]
            o = c_slice[0]
            c = c_slice[-1]
            h = h_slice.max()
            lo = l_slice.min()
            chg = (c - o) / o * 100 if o > 0 else 0
            lines.append(f"  -{i * 6:>3}h | {o:.2f} | {h:.2f} | {lo:.2f} | {c:.2f} | {chg:+.2f}%")
        lines.append("")

    # --- Funding rate trend (last 24 values = 2 hours of 5-min bars) ---
    funding_arr = store.funding_rates
    nf = len(funding_arr)
    lines.append("### Funding Rate Trend (last 24 values)")
    if nf >= 2:
        tail = funding_arr[-24:]
        mean_funding = tail.mean()
        direction = "rising" if tail[-1] > tail[0] else "falling"
        # Show first, middle, last for compactness
        if len(tail) >= 6:
            sampled = [tail[0], tail[len(tail)//4], tail[len(tail)//2], tail[3*len(tail)//4], tail[-1]]
            vals_str = "  ".join(f"{v:.6f}" for v in sampled)
            lines.append(f"  sampled(5): {vals_str}")
        else:
            vals_str = "  ".join(f"{v:.6f}" for v in tail)
            lines.append(f"  values: {vals_str}")
        lines.append(f"  mean={mean_funding:.6f}  direction={direction}  points={len(tail)}")
    elif nf == 1:
        lines.append(f"  current={funding_arr[0]:.6f}  (insufficient history)")
    else:
        lines.append(f"  current={float(snapshot.funding_rate):.6f}  (no history)")
    lines.append("")

    # --- Open interest trend (last 24 samples = 2 hours) ---
    oi_arr = store.open_interests
    noi = len(oi_arr)
    lines.append("### Open Interest Trend (last 24 samples)")
    if noi >= 2:
        tail_oi = oi_arr[-24:]
        oi_first = tail_oi[0]
        oi_last = tail_oi[-1]
        pct_chg = ((oi_last - oi_first) / oi_first * 100) if oi_first != 0 else 0.0
        oi_min = tail_oi.min()
        oi_max = tail_oi.max()
        lines.append(f"  current={oi_last:.0f}  min={oi_min:.0f}  max={oi_max:.0f}")
        lines.append(f"  pct_change={pct_chg:+.2f}%  points={len(tail_oi)}")
    else:
        lines.append(f"  current={float(snapshot.open_interest):.0f}  (insufficient history)")
    lines.append("")

    # --- Volatility & orderbook ---
    lines.append("### Volatility & Orderbook")
    lines.append(f"  volatility_1h={snapshot.volatility_1h:.4f}")
    lines.append(f"  volatility_24h={snapshot.volatility_24h:.4f}")
    lines.append(f"  orderbook_imbalance={snapshot.orderbook_imbalance:+.4f}")
    lines.append(f"  spread_bps={snapshot.spread_bps:.2f}")
    lines.append("")

    # --- Current snapshot summary ---
    lines.append("### Current Snapshot")
    lines.append(f"  best_bid={float(snapshot.best_bid):.2f}  best_ask={float(snapshot.best_ask):.2f}")
    lines.append(f"  funding_rate={float(snapshot.funding_rate):.6f}")
    lines.append(f"  open_interest={float(snapshot.open_interest):.0f}")
    lines.append(f"  hours_since_last_funding={snapshot.hours_since_last_funding:.2f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def validate_claude_response(
    raw: dict[str, Any],
    snapshot: MarketSnapshot,
    store: FeatureStore,
) -> dict[str, Any] | None:
    """Validate and normalise Claude's structured analysis response.

    Performs the following checks and transformations:
    - direction=NO_SIGNAL → return None (not an error)
    - conviction clamped to [0.0, 1.0]
    - entry_price within ±5% of mark_price (ATR default if missing/invalid)
    - stop_loss and take_profit directional sanity checks (ATR defaults if absent)

    Args:
        raw: Tool input dict from Claude's response.
        snapshot: Current market snapshot (provides mark_price reference).
        store: Feature store (provides highs/lows/closes for ATR computation).

    Returns:
        Validated, normalised dict with Decimal prices, or None if direction
        is NO_SIGNAL or validation cannot be satisfied.
    """
    direction_str = raw.get("direction", "NO_SIGNAL")
    if direction_str == "NO_SIGNAL":
        _logger.debug("claude_no_signal", instrument=raw.get("instrument"))
        # Return partial dict so callers can persist Claude's reasoning for observability
        return {
            "direction": "NO_SIGNAL",
            "conviction": 0.0,
            "reasoning": str(raw.get("reasoning", "No clear trade opportunity.")),
        }

    if direction_str not in ("LONG", "SHORT"):
        _logger.warning(
            "claude_invalid_direction",
            direction=direction_str,
            instrument=raw.get("instrument"),
        )
        return None

    direction = PositionSide(direction_str)
    mark_price = snapshot.mark_price

    # Clamp conviction
    raw_conviction = float(raw.get("conviction", 0.0))
    conviction = max(0.0, min(1.0, raw_conviction))

    # --- ATR for fallback prices ---
    highs = store.highs
    lows = store.lows
    closes = store.closes
    atr_value: float | None = None
    if len(closes) >= _ATR_PERIOD:
        atr_arr = atr(highs, lows, closes, period=_ATR_PERIOD)
        last_atr = atr_arr[-1]
        if not np.isnan(last_atr):
            atr_value = float(last_atr)

    # --- Entry price ---
    raw_entry = raw.get("entry_price")
    entry_price: Decimal

    if raw_entry is not None:
        entry_candidate = Decimal(str(raw_entry))
        deviation = abs(entry_candidate - mark_price) / mark_price
        if deviation > _ENTRY_PRICE_MAX_DEVIATION:
            _logger.warning(
                "claude_entry_price_out_of_range",
                entry=str(entry_candidate),
                mark=str(mark_price),
                deviation_pct=f"{float(deviation) * 100:.2f}%",
            )
            entry_price = mark_price  # fall back to mark
        else:
            entry_price = entry_candidate
    else:
        entry_price = mark_price

    # Tick-size rounding (best-effort — use instrument config if available)
    try:
        inst_cfg = get_instrument(snapshot.instrument)
        entry_price = _tick_round(entry_price, inst_cfg.tick_size)
    except KeyError:
        pass  # instrument not loaded yet (e.g. unit tests)

    # --- Stop loss ---
    raw_sl = raw.get("stop_loss")
    stop_loss: Decimal | None = None

    if raw_sl is not None:
        sl_candidate = Decimal(str(raw_sl))
        if _sl_valid(direction, entry_price, sl_candidate):
            stop_loss = sl_candidate
        else:
            _logger.debug(
                "claude_stop_loss_invalid_direction",
                direction=direction_str,
                entry=str(entry_price),
                stop_loss=str(sl_candidate),
            )

    if stop_loss is None:
        stop_loss = _atr_stop(direction, entry_price, atr_value)

    # --- Take profit ---
    raw_tp = raw.get("take_profit")
    take_profit: Decimal | None = None

    if raw_tp is not None:
        tp_candidate = Decimal(str(raw_tp))
        if _tp_valid(direction, entry_price, tp_candidate):
            take_profit = tp_candidate
        else:
            _logger.debug(
                "claude_take_profit_invalid_direction",
                direction=direction_str,
                entry=str(entry_price),
                take_profit=str(tp_candidate),
            )

    if take_profit is None:
        take_profit = _atr_take_profit(direction, entry_price, atr_value)

    # Apply tick rounding to stops/TP as well
    try:
        inst_cfg = get_instrument(snapshot.instrument)
        if stop_loss is not None:
            stop_loss = _tick_round(stop_loss, inst_cfg.tick_size)
        if take_profit is not None:
            take_profit = _tick_round(take_profit, inst_cfg.tick_size)
    except KeyError:
        pass

    return {
        "instrument": raw.get("instrument", snapshot.instrument),
        "direction": direction,
        "conviction": conviction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "time_horizon_hours": float(raw.get("time_horizon_hours", 4.0)),
        "reasoning": str(raw.get("reasoning", "")),
    }


# ---------------------------------------------------------------------------
# Async API call
# ---------------------------------------------------------------------------


async def call_claude_analysis(
    instrument_id: str,
    store: FeatureStore,
    snapshot: MarketSnapshot,
    regime: MarketRegime,
) -> dict[str, Any] | None:
    """Call Claude API asynchronously and return a validated analysis dict.

    Uses ``anthropic.AsyncAnthropic()`` with forced ``submit_market_analysis``
    tool use.  Validates the response before returning.

    Args:
        instrument_id: Instrument being analysed (e.g. 'ETH-PERP').
        store: Feature store with rolling history for context assembly.
        snapshot: Current market snapshot.
        regime: Currently detected market regime.

    Returns:
        Validated dict with keys (instrument, direction, conviction,
        entry_price, stop_loss, take_profit, time_horizon_hours, reasoning),
        or None if Claude returned NO_SIGNAL, validation failed, or any
        API/parsing error occurred.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _logger.error(
            "claude_api_key_missing",
            instrument=instrument_id,
        )
        return None

    context = build_market_context(instrument_id, store, snapshot, regime)
    system_prompt = build_system_prompt()
    user_message = (
        f"Analyse the following market data for {instrument_id} and decide "
        f"whether there is a clear trade opportunity right now.\n\n{context}"
    )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[MARKET_ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "submit_market_analysis"},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        _logger.error(
            "claude_api_error",
            instrument=instrument_id,
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
        _logger.warning(
            "claude_no_tool_use_block",
            instrument=instrument_id,
        )
        return None

    raw: dict[str, Any] = tool_use_block.input  # type: ignore[assignment]
    _logger.debug(
        "claude_raw_response",
        instrument=instrument_id,
        direction=raw.get("direction"),
        conviction=raw.get("conviction"),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
        cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", 0),
    )

    validated = validate_claude_response(raw, snapshot, store)
    if validated is not None and validated.get("direction") != "NO_SIGNAL":
        _logger.info(
            "claude_analysis_complete",
            instrument=instrument_id,
            direction=str(validated["direction"].value),
            conviction=f"{validated['conviction']:.3f}",
            time_horizon_hours=validated["time_horizon_hours"],
        )

    return validated


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _sl_valid(direction: PositionSide, entry: Decimal, stop_loss: Decimal) -> bool:
    """Return True if stop_loss is directionally valid."""
    if direction == PositionSide.LONG:
        return stop_loss < entry
    return stop_loss > entry


def _tp_valid(direction: PositionSide, entry: Decimal, take_profit: Decimal) -> bool:
    """Return True if take_profit is directionally valid."""
    if direction == PositionSide.LONG:
        return take_profit > entry
    return take_profit < entry


def _atr_stop(
    direction: PositionSide, entry: Decimal, atr_val: float | None
) -> Decimal | None:
    """Compute ATR-based stop-loss, or return None if ATR unavailable."""
    if atr_val is None:
        return None
    offset = Decimal(str(atr_val * _DEFAULT_STOP_ATR_MULT))
    if direction == PositionSide.LONG:
        return entry - offset
    return entry + offset


def _atr_take_profit(
    direction: PositionSide, entry: Decimal, atr_val: float | None
) -> Decimal | None:
    """Compute ATR-based take-profit, or return None if ATR unavailable."""
    if atr_val is None:
        return None
    offset = Decimal(str(atr_val * _DEFAULT_TP_ATR_MULT))
    if direction == PositionSide.LONG:
        return entry + offset
    return entry - offset


def _tick_round(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round a price to the nearest tick (floor to tick size)."""
    from libs.common.utils import round_to_tick

    return round_to_tick(price, tick_size)
