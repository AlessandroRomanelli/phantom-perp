"""Momentum strategy — multi-timeframe EMA crossover with ADX trend filter.

Signal logic:
  1. Compute fast EMA and slow EMA on the price buffer.
  2. Detect crossovers (fast crosses above slow -> bullish, below -> bearish).
  3. Require ADX > threshold to confirm a trending market.
  4. Volume confirmation: reject crossovers with low bar volume (MOM-01).
  5. RSI confirmation: reject long if overbought, reject short if oversold.
  6. Adaptive conviction: ADX (0-0.35) + RSI (0-0.35) + vol/volatility (0-0.30) (MOM-02).
  7. Swing point stops with ATR fallback (MOM-03).
  8. Portfolio A routing for high-conviction signals (MOM-04).
  9. Funding rate boost for conviction when funding aligns with direction (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray

from libs.common.instruments import get_instrument
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.moving_averages import ema
from libs.indicators.oscillators import adx, rsi
from libs.indicators.volatility import atr

from libs.common.logging import setup_logging

from agents.signals.adaptive_conviction import compute_adaptive_threshold
from agents.signals.feature_store import FeatureStore
from agents.signals.funding_filter import compute_funding_boost
from agents.signals.strategies.base import SignalStrategy
from agents.signals.swing_points import find_swing_high, find_swing_low

_log = setup_logging("momentum_debug", json_output=False)


@dataclass
class MomentumParams:
    """Tunable parameters for the momentum strategy."""

    fast_ema_period: int = 12
    slow_ema_period: int = 26
    adx_period: int = 14
    adx_threshold: float = 20.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    min_conviction: float = 0.5
    cooldown_bars: int = 5
    # Phase 2 additions
    vol_lookback: int = 10
    vol_min_ratio: float = 0.5
    portfolio_a_min_conviction: float = 0.75
    swing_lookback: int = 20
    swing_order: int = 3
    # Phase 4 additions: funding rate boost
    funding_rate_boost: float = 0.08
    funding_z_score_threshold: float = 1.5
    funding_min_samples: int = 10


class MomentumStrategy(SignalStrategy):
    """Multi-timeframe EMA crossover with ADX filter, volume confirmation,
    adaptive conviction, swing stops, and Portfolio A routing.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: MomentumParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or MomentumParams()

        if config:
            p = config.get("parameters", {})
            self._params = MomentumParams(
                fast_ema_period=p.get("fast_ema_period", self._params.fast_ema_period),
                slow_ema_period=p.get("slow_ema_period", self._params.slow_ema_period),
                adx_period=p.get("adx_period", self._params.adx_period),
                adx_threshold=p.get("adx_threshold", self._params.adx_threshold),
                rsi_period=p.get("rsi_period", self._params.rsi_period),
                rsi_overbought=p.get("rsi_overbought", self._params.rsi_overbought),
                rsi_oversold=p.get("rsi_oversold", self._params.rsi_oversold),
                atr_period=p.get("atr_period", self._params.atr_period),
                stop_loss_atr_mult=p.get(
                    "stop_loss_atr_mult", self._params.stop_loss_atr_mult,
                ),
                take_profit_atr_mult=p.get(
                    "take_profit_atr_mult", self._params.take_profit_atr_mult,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
                vol_lookback=p.get("vol_lookback", self._params.vol_lookback),
                vol_min_ratio=p.get("vol_min_ratio", self._params.vol_min_ratio),
                portfolio_a_min_conviction=p.get(
                    "portfolio_a_min_conviction", self._params.portfolio_a_min_conviction,
                ),
                swing_lookback=p.get("swing_lookback", self._params.swing_lookback),
                swing_order=p.get("swing_order", self._params.swing_order),
                funding_rate_boost=p.get(
                    "funding_rate_boost", self._params.funding_rate_boost,
                ),
                funding_z_score_threshold=p.get(
                    "funding_z_score_threshold", self._params.funding_z_score_threshold,
                ),
                funding_min_samples=p.get(
                    "funding_min_samples", self._params.funding_min_samples,
                ),
            )

        self._enabled = True
        self._bars_since_signal = self._params.cooldown_bars  # Start ready

    @property
    def name(self) -> str:
        return "momentum"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        # Need enough data for the slowest indicator
        return self._params.slow_ema_period + self._params.adx_period + 5

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate EMA crossover, volume filter, ADX filter, and RSI confirmation."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        tick_size = get_instrument(snapshot.instrument).tick_size
        closes = store.closes
        highs = store.highs
        lows = store.lows

        # Compute indicators
        fast = ema(closes, p.fast_ema_period)
        slow = ema(closes, p.slow_ema_period)
        adx_vals = adx(highs, lows, closes, p.adx_period)
        rsi_vals = rsi(closes, p.rsi_period)
        atr_vals = atr(highs, lows, closes, p.atr_period)

        # Current and previous values
        cur_fast = fast[-1]
        cur_slow = slow[-1]
        prev_fast = fast[-2]
        prev_slow = slow[-2]
        cur_adx = adx_vals[-1]
        cur_rsi = rsi_vals[-1]
        cur_atr = atr_vals[-1]

        # Need valid indicator values
        if any(np.isnan(v) for v in [cur_fast, cur_slow, prev_fast, prev_slow, cur_atr]):
            return []

        # ADX filter: skip if no trend (allow NaN ADX to pass with reduced conviction)
        adx_valid = not np.isnan(cur_adx)

        # Debug: log indicator values periodically
        if self._bars_since_signal % 10 == 0:
            _log.info(
                "momentum_indicators",
                samples=store.sample_count,
                fast_ema=round(cur_fast, 2),
                slow_ema=round(cur_slow, 2),
                ema_diff=round(cur_fast - cur_slow, 4),
                adx=round(cur_adx, 2) if adx_valid else "NaN",
                adx_threshold=p.adx_threshold,
                rsi=round(cur_rsi, 2),
                atr=round(cur_atr, 4),
                price=round(closes[-1], 2),
            )

        if adx_valid and cur_adx < p.adx_threshold:
            return []

        # Detect crossover
        bullish_cross = prev_fast <= prev_slow and cur_fast > cur_slow
        bearish_cross = prev_fast >= prev_slow and cur_fast < cur_slow

        if not bullish_cross and not bearish_cross:
            return []

        # Volume confirmation (MOM-01)
        bar_vols = store.bar_volumes
        if len(bar_vols) < p.vol_lookback:
            return []
        recent_vols = np.abs(bar_vols[-p.vol_lookback :])
        vol_avg = float(np.mean(recent_vols))
        cur_vol = float(np.abs(bar_vols[-1]))
        if vol_avg > 0 and cur_vol < vol_avg * p.vol_min_ratio:
            return []  # Low volume, likely false breakout
        volume_ratio = cur_vol / vol_avg if vol_avg > 0 else 1.0

        # RSI confirmation
        rsi_valid = not np.isnan(cur_rsi)
        if bullish_cross and rsi_valid and cur_rsi > p.rsi_overbought:
            return []  # Don't go long when overbought
        if bearish_cross and rsi_valid and cur_rsi < p.rsi_oversold:
            return []  # Don't go short when oversold

        # Compute adaptive conviction (MOM-02)
        conviction = self._compute_conviction(
            cur_adx if adx_valid else 20.0,
            cur_rsi if rsi_valid else 50.0,
            bullish_cross,
            volume_ratio=volume_ratio,
            atr_vals=atr_vals,
            cur_atr=cur_atr,
        )

        # Funding rate boost (Phase 4)
        direction = PositionSide.LONG if bullish_cross else PositionSide.SHORT
        funding_result = compute_funding_boost(
            funding_rates=store.funding_rates,
            signal_direction=direction,
            hours_since_last_funding=snapshot.hours_since_last_funding,
            z_score_threshold=p.funding_z_score_threshold,
            max_boost=p.funding_rate_boost,
            min_samples=p.funding_min_samples,
        )
        if funding_result.boost > 0:
            conviction = min(conviction + funding_result.boost, 1.0)
            conviction = round(conviction, 3)

        if conviction < p.min_conviction:
            return []

        # Build signal
        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        # Swing point stops (MOM-03)
        swing: float | None = None
        if direction == PositionSide.LONG:
            swing = find_swing_low(lows, p.swing_lookback, p.swing_order)
            if swing is not None and Decimal(str(swing)) < entry:
                stop_loss = round_to_tick(Decimal(str(swing)), tick_size)
            else:
                swing = None  # Mark as not used
                stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)
        else:
            swing = find_swing_high(highs, p.swing_lookback, p.swing_order)
            if swing is not None and Decimal(str(swing)) > entry:
                stop_loss = round_to_tick(Decimal(str(swing)), tick_size)
            else:
                swing = None  # Mark as not used
                stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)

        # Portfolio A routing (MOM-04)
        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        # Compute volatility percentile for metadata
        adaptive_meta = compute_adaptive_threshold(
            atr_vals, cur_atr, 1.0, min_samples=1,
        )
        vol_pct = adaptive_meta.volatility_percentile

        reasoning = (
            f"EMA crossover {'bullish' if bullish_cross else 'bearish'}: "
            f"fast({p.fast_ema_period})={cur_fast:.2f} vs slow({p.slow_ema_period})={cur_slow:.2f}, "
            f"ADX={cur_adx:.1f}" + (f", RSI={cur_rsi:.1f}" if rsi_valid else "")
            + f", vol_ratio={volume_ratio:.2f}"
        )

        metadata: dict[str, object] = {
            "fast_ema": round(cur_fast, 2),
            "slow_ema": round(cur_slow, 2),
            "adx": round(cur_adx, 1) if adx_valid else None,
            "rsi": round(cur_rsi, 1) if rsi_valid else None,
            "atr": round(cur_atr, 2),
            "volume_ratio": round(volume_ratio, 3),
            "vol_percentile": round(vol_pct, 3),
            "swing_stop": swing is not None,
        }
        if funding_result.boost > 0:
            metadata["funding_boost"] = funding_result.boost
            metadata["funding_zscore"] = funding_result.z_score

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=4),
            reasoning=reasoning,
            suggested_target=suggested_target,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )

        self._bars_since_signal = 0
        return [signal]

    def _compute_conviction(
        self,
        adx_value: float,
        rsi_value: float,
        is_bullish: bool,
        volume_ratio: float = 1.0,
        atr_vals: NDArray[np.float64] | None = None,
        cur_atr: float = 0.0,
    ) -> float:
        """Compute conviction from ADX, RSI, and volume/volatility components.

        Three-component model (MOM-02):
          - ADX component (0-0.35): scales from 0 at ADX=20 to 0.35 at ADX=80+.
          - RSI component (0-0.35): scales based on how much RSI agrees with direction.
          - Volume/volatility component (0-0.30): combines volume_ratio and ATR percentile.
        """
        # ADX component: stronger trend = higher conviction (0 to 0.35)
        adx_score = min((adx_value - 20.0) / 60.0 * 0.35, 0.35)
        adx_score = max(adx_score, 0.0)

        # RSI component: RSI alignment with direction (0 to 0.35)
        if is_bullish:
            # For bullish: RSI 30-50 is ideal (not overbought, room to run)
            rsi_score = max(0.0, min((70.0 - rsi_value) / 80.0 * 0.35, 0.35))
        else:
            # For bearish: RSI 50-70 is ideal
            rsi_score = max(0.0, min((rsi_value - 30.0) / 80.0 * 0.35, 0.35))

        # Volume/volatility component (0 to 0.30)
        # Volume ratio contribution: higher volume = higher conviction
        vol_ratio_score = min(max((volume_ratio - 0.5) / 2.0, 0.0), 0.15)

        # ATR percentile contribution: high volatility breakouts score higher
        vol_pct = 0.5  # Default if no ATR data
        if atr_vals is not None:
            adaptive_result = compute_adaptive_threshold(
                atr_vals, cur_atr, 1.0, min_samples=1,
            )
            vol_pct = adaptive_result.volatility_percentile
        atr_pct_score = min(vol_pct * 0.15, 0.15)

        vol_score = vol_ratio_score + atr_pct_score

        return round(min(adx_score + rsi_score + vol_score, 1.0), 3)

