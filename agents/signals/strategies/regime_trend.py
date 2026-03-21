"""Regime-filtered trend following strategy.

Only trades when three independent filters ALL agree:
  1. Higher-timeframe trend — long-period EMA slope + price position + ADX.
  2. Volatility expansion — ATR must be above its own moving average.
  3. Spot confirmation — index price EMA slope must agree with direction.

Once all filters pass, two entry patterns are checked:
  - Breakout: price exceeds the Donchian high/low of the lookback window.
  - Pullback: price retraces to the fast EMA and bounces in trend direction.

The filter stack is the edge. A plain momentum bot is weaker; a momentum
bot that only trades when trend, vol, and spot confirmation agree is much
stronger — especially in the current ETH market which trends well when
flows line up but is not a clean one-way environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from libs.common.constants import INSTRUMENT_ID
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.moving_averages import ema, sma
from libs.indicators.oscillators import adx
from libs.indicators.volatility import atr

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy


@dataclass
class RegimeTrendParams:
    """Tunable parameters for the regime-filtered trend strategy."""

    # ── Higher-timeframe trend filter ──
    trend_ema_period: int = 50
    trend_slope_lookback: int = 5
    adx_period: int = 14
    adx_threshold: float = 22.0

    # ── Volatility expansion filter ──
    atr_period: int = 14
    atr_avg_period: int = 30
    atr_expansion_threshold: float = 1.1

    # ── Spot confirmation filter ──
    spot_ema_period: int = 20
    spot_slope_lookback: int = 5

    # ── Entry patterns ──
    fast_ema_period: int = 20
    breakout_lookback: int = 20
    pullback_tolerance_atr: float = 0.3

    # ── Risk management (Portfolio B — default) ──
    stop_loss_atr_mult: float = 2.5
    take_profit_atr_mult: float = 4.0

    # ── Portfolio A autonomous routing ──
    portfolio_a_enabled: bool = True
    portfolio_a_min_conviction: float = 0.7
    portfolio_a_breakout_only: bool = True
    portfolio_a_stop_loss_atr_mult: float = 1.5
    portfolio_a_take_profit_atr_mult: float = 2.5

    # ── Signal control ──
    min_conviction: float = 0.5
    cooldown_bars: int = 8


class RegimeTrendStrategy(SignalStrategy):
    """Regime-filtered trend following with breakout and pullback entries.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: RegimeTrendParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or RegimeTrendParams()

        if config:
            p = config.get("parameters", {})
            self._params = RegimeTrendParams(
                trend_ema_period=p.get("trend_ema_period", self._params.trend_ema_period),
                trend_slope_lookback=p.get("trend_slope_lookback", self._params.trend_slope_lookback),
                adx_period=p.get("adx_period", self._params.adx_period),
                adx_threshold=p.get("adx_threshold", self._params.adx_threshold),
                atr_period=p.get("atr_period", self._params.atr_period),
                atr_avg_period=p.get("atr_avg_period", self._params.atr_avg_period),
                atr_expansion_threshold=p.get("atr_expansion_threshold", self._params.atr_expansion_threshold),
                spot_ema_period=p.get("spot_ema_period", self._params.spot_ema_period),
                spot_slope_lookback=p.get("spot_slope_lookback", self._params.spot_slope_lookback),
                fast_ema_period=p.get("fast_ema_period", self._params.fast_ema_period),
                breakout_lookback=p.get("breakout_lookback", self._params.breakout_lookback),
                pullback_tolerance_atr=p.get("pullback_tolerance_atr", self._params.pullback_tolerance_atr),
                stop_loss_atr_mult=p.get("stop_loss_atr_mult", self._params.stop_loss_atr_mult),
                take_profit_atr_mult=p.get("take_profit_atr_mult", self._params.take_profit_atr_mult),
                portfolio_a_enabled=p.get("portfolio_a_enabled", self._params.portfolio_a_enabled),
                portfolio_a_min_conviction=p.get("portfolio_a_min_conviction", self._params.portfolio_a_min_conviction),
                portfolio_a_breakout_only=p.get("portfolio_a_breakout_only", self._params.portfolio_a_breakout_only),
                portfolio_a_stop_loss_atr_mult=p.get("portfolio_a_stop_loss_atr_mult", self._params.portfolio_a_stop_loss_atr_mult),
                portfolio_a_take_profit_atr_mult=p.get("portfolio_a_take_profit_atr_mult", self._params.portfolio_a_take_profit_atr_mult),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
            )

        self._enabled = True
        self._bars_since_signal = self._params.cooldown_bars  # Start ready

    # ── SignalStrategy interface ─────────────────────────────────────────

    @property
    def name(self) -> str:
        return "regime_trend"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return (
            max(
                self._params.trend_ema_period,
                self._params.atr_avg_period + self._params.atr_period,
                self._params.spot_ema_period,
                self._params.fast_ema_period,
                self._params.breakout_lookback,
                2 * self._params.adx_period,
            )
            + self._params.trend_slope_lookback
            + 5
        )

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Run the three-filter stack, then check entry patterns."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        closes = store.closes
        highs = store.highs
        lows = store.lows
        index_prices = store.index_prices

        # ── Compute indicators ──────────────────────────────────────────

        trend_ema_vals = ema(closes, p.trend_ema_period)
        fast_ema_vals = ema(closes, p.fast_ema_period)
        adx_vals = adx(highs, lows, closes, p.adx_period)
        atr_vals = atr(highs, lows, closes, p.atr_period)
        # SMA over the valid (non-NaN) portion of ATR to avoid NaN poisoning
        atr_valid_start = p.atr_period - 1
        atr_avg = np.full_like(atr_vals, np.nan)
        if len(atr_vals) > atr_valid_start + p.atr_avg_period:
            valid_atr = atr_vals[atr_valid_start:]
            atr_avg[atr_valid_start:] = sma(valid_atr, p.atr_avg_period)
        spot_ema_vals = ema(index_prices, p.spot_ema_period)

        # Current values
        cur_trend_ema = trend_ema_vals[-1]
        cur_fast_ema = fast_ema_vals[-1]
        cur_adx = adx_vals[-1]
        cur_atr = atr_vals[-1]
        cur_atr_avg = atr_avg[-1]
        cur_spot_ema = spot_ema_vals[-1]
        cur_close = closes[-1]

        # Bail on NaN from any critical indicator
        critical = [cur_trend_ema, cur_fast_ema, cur_atr, cur_atr_avg, cur_spot_ema]
        if any(np.isnan(v) for v in critical):
            return []

        # ── Filter 1: Higher-timeframe trend ────────────────────────────

        # EMA slope over the lookback window
        lb = p.trend_slope_lookback
        prev_trend_ema = trend_ema_vals[-1 - lb]
        if np.isnan(prev_trend_ema):
            return []

        trend_slope = cur_trend_ema - prev_trend_ema
        price_above_trend = cur_close > cur_trend_ema
        price_below_trend = cur_close < cur_trend_ema

        trend_up = trend_slope > 0 and price_above_trend
        trend_down = trend_slope < 0 and price_below_trend

        if not trend_up and not trend_down:
            return []

        # ADX confirms trending (allow NaN ADX to pass with reduced conviction)
        adx_valid = not np.isnan(cur_adx)
        if adx_valid and cur_adx < p.adx_threshold:
            return []

        # ── Filter 2: Volatility expansion ──────────────────────────────

        if cur_atr_avg <= 0:
            return []

        atr_ratio = cur_atr / cur_atr_avg
        if atr_ratio < p.atr_expansion_threshold:
            return []

        # ── Filter 3: Spot confirmation ─────────────────────────────────

        prev_spot_ema = spot_ema_vals[-1 - p.spot_slope_lookback]
        if np.isnan(prev_spot_ema):
            return []

        spot_slope = cur_spot_ema - prev_spot_ema
        spot_confirms_long = spot_slope > 0
        spot_confirms_short = spot_slope < 0

        if trend_up and not spot_confirms_long:
            return []
        if trend_down and not spot_confirms_short:
            return []

        # ── All filters passed — check entry patterns ───────────────────

        direction: PositionSide | None = None
        entry_type: str | None = None

        if trend_up:
            direction, entry_type = self._check_long_entries(
                closes, highs, lows, cur_fast_ema, cur_atr, p,
            )
        else:
            direction, entry_type = self._check_short_entries(
                closes, highs, lows, cur_fast_ema, cur_atr, p,
            )

        if direction is None:
            return []

        # ── Conviction ──────────────────────────────────────────────────

        conviction = self._compute_conviction(
            adx_value=cur_adx if adx_valid else 25.0,
            atr_ratio=atr_ratio,
            trend_slope=abs(trend_slope),
            spot_slope=abs(spot_slope),
            cur_atr=cur_atr,
            entry_type=entry_type,
        )

        if conviction < p.min_conviction:
            return []

        # ── Build signals ───────────────────────────────────────────────

        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))
        side_label = "LONG" if direction == PositionSide.LONG else "SHORT"
        now = utc_now()

        base_metadata = {
            "entry_type": entry_type,
            "trend_ema": round(cur_trend_ema, 2),
            "trend_slope": round(trend_slope, 4),
            "fast_ema": round(cur_fast_ema, 2),
            "adx": round(cur_adx, 1) if adx_valid else None,
            "atr": round(cur_atr, 2),
            "atr_ratio": round(atr_ratio, 3),
            "spot_ema": round(cur_spot_ema, 2),
            "spot_slope": round(spot_slope, 4),
        }

        signals: list[StandardSignal] = []

        # Portfolio B signal (wider stops, longer horizon)
        if direction == PositionSide.LONG:
            sl_b = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)))
            tp_b = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)))
        else:
            sl_b = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)))
            tp_b = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)))

        reasoning_b = (
            f"Regime-filtered {side_label} ({entry_type}): "
            f"trend EMA({p.trend_ema_period}) slope={'up' if trend_up else 'down'}, "
            f"ADX={cur_adx:.1f}" + (f" (>{p.adx_threshold})" if adx_valid else " (N/A)") + ", "
            f"ATR expansion {atr_ratio:.2f}x, "
            f"spot confirmed"
        )

        signals.append(StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=now,
            instrument=INSTRUMENT_ID,
            direction=direction,
            conviction=conviction,
            source=SignalSource.REGIME_TREND,
            time_horizon=timedelta(hours=6),
            reasoning=reasoning_b,
            suggested_target=PortfolioTarget.B,
            entry_price=entry,
            stop_loss=sl_b,
            take_profit=tp_b,
            metadata={**base_metadata, "portfolio": "B"},
        ))

        # Portfolio A signal (tighter stops, shorter horizon, breakout-only by default)
        a_eligible = (
            p.portfolio_a_enabled
            and conviction >= p.portfolio_a_min_conviction
            and (not p.portfolio_a_breakout_only or entry_type == "breakout")
        )

        if a_eligible:
            if direction == PositionSide.LONG:
                sl_a = round_to_tick(entry - atr_d * Decimal(str(p.portfolio_a_stop_loss_atr_mult)))
                tp_a = round_to_tick(entry + atr_d * Decimal(str(p.portfolio_a_take_profit_atr_mult)))
            else:
                sl_a = round_to_tick(entry + atr_d * Decimal(str(p.portfolio_a_stop_loss_atr_mult)))
                tp_a = round_to_tick(entry - atr_d * Decimal(str(p.portfolio_a_take_profit_atr_mult)))

            signals.append(StandardSignal(
                signal_id=generate_id("sig"),
                timestamp=now,
                instrument=INSTRUMENT_ID,
                direction=direction,
                conviction=conviction,
                source=SignalSource.REGIME_TREND,
                time_horizon=timedelta(hours=2),
                reasoning=f"[Auto] {reasoning_b}",
                suggested_target=PortfolioTarget.A,
                entry_price=entry,
                stop_loss=sl_a,
                take_profit=tp_a,
                metadata={**base_metadata, "portfolio": "A"},
            ))

        self._bars_since_signal = 0
        return signals

    # ── Entry pattern detection ─────────────────────────────────────────

    @staticmethod
    def _check_long_entries(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        fast_ema: float,
        cur_atr: float,
        p: RegimeTrendParams,
    ) -> tuple[PositionSide | None, str | None]:
        """Check for long breakout or pullback entry."""
        cur_close = closes[-1]
        cur_high = highs[-1]
        cur_low = lows[-1]

        # Breakout: close exceeds highest high of lookback (excluding current bar)
        lookback_highs = highs[-1 - p.breakout_lookback : -1]
        if len(lookback_highs) >= p.breakout_lookback:
            donchian_high = np.max(lookback_highs)
            if cur_close > donchian_high:
                return PositionSide.LONG, "breakout"

        # Pullback: low dips near/below fast EMA, close recovers above it
        tolerance = cur_atr * p.pullback_tolerance_atr
        touched_ema = cur_low <= fast_ema + tolerance
        closed_above = cur_close > fast_ema
        if touched_ema and closed_above:
            return PositionSide.LONG, "pullback"

        return None, None

    @staticmethod
    def _check_short_entries(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        fast_ema: float,
        cur_atr: float,
        p: RegimeTrendParams,
    ) -> tuple[PositionSide | None, str | None]:
        """Check for short breakout or pullback entry."""
        cur_close = closes[-1]
        cur_high = highs[-1]
        cur_low = lows[-1]

        # Breakout: close drops below lowest low of lookback
        lookback_lows = lows[-1 - p.breakout_lookback : -1]
        if len(lookback_lows) >= p.breakout_lookback:
            donchian_low = np.min(lookback_lows)
            if cur_close < donchian_low:
                return PositionSide.SHORT, "breakout"

        # Pullback: high reaches near/above fast EMA, close rejects below it
        tolerance = cur_atr * p.pullback_tolerance_atr
        touched_ema = cur_high >= fast_ema - tolerance
        closed_below = cur_close < fast_ema
        if touched_ema and closed_below:
            return PositionSide.SHORT, "pullback"

        return None, None

    # ── Conviction scoring ──────────────────────────────────────────────

    @staticmethod
    def _compute_conviction(
        adx_value: float,
        atr_ratio: float,
        trend_slope: float,
        spot_slope: float,
        cur_atr: float,
        entry_type: str,
    ) -> float:
        """Compute conviction from filter strength and entry quality.

        Components (each capped, sum capped at 1.0):
          - ADX strength:           0 - 0.30
          - Volatility expansion:   0 - 0.20
          - Spot-perp alignment:    0 - 0.20
          - Entry quality:          0 - 0.30
        """
        # ADX: scales from 0 at threshold (22) to 0.30 at ADX=50+
        adx_score = min((adx_value - 22.0) / 93.0, 0.3)
        adx_score = max(adx_score, 0.0)

        # Vol expansion: ATR ratio 1.1 → 0, ratio 2.0+ → 0.20
        vol_score = min((atr_ratio - 1.1) / 4.5, 0.2)
        vol_score = max(vol_score, 0.0)

        # Spot alignment: normalized by ATR so it's scale-independent
        if cur_atr > 0:
            spot_norm = abs(spot_slope) / cur_atr
            spot_score = min(spot_norm / 5.0, 0.2)
        else:
            spot_score = 0.0
        spot_score = max(spot_score, 0.0)

        # Entry quality: breakout scores higher than pullback
        entry_score = 0.25 if entry_type == "breakout" else 0.15

        return round(min(adx_score + vol_score + spot_score + entry_score, 1.0), 3)
