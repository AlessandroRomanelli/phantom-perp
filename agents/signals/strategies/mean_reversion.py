"""Mean reversion strategy — Bollinger Band deviation with RSI confirmation.

Signal logic:
  1. Compute Bollinger Bands (adaptive width based on volatility percentile).
  2. Price below lower band -> LONG (expect reversion to mean).
  3. Price above upper band -> SHORT (expect reversion to mean).
  4. Multi-factor trend filter: reject if composite trend strength exceeds threshold.
  5. RSI confirmation: require oversold for longs, overbought for shorts.
  6. Conviction scales with distance beyond band + RSI extremity + volume.
  7. Stop-loss at ATR multiple beyond entry; take-profit at middle band or extended.
  8. Strong reversions get extended take-profit beyond middle band.
  9. High-conviction signals route to Portfolio A.
  10. Funding rate boost for conviction when funding aligns with direction (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray

from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.moving_averages import ema
from libs.indicators.oscillators import adx, rsi
from libs.indicators.volatility import atr, bollinger_bands

from libs.common.logging import setup_logging

from agents.signals.adaptive_conviction import compute_adaptive_threshold
from agents.signals.feature_store import FeatureStore
from agents.signals.funding_filter import compute_funding_boost
from agents.signals.strategies.base import SignalStrategy

_log = setup_logging("mr_debug", json_output=False)


@dataclass
class MeanReversionParams:
    """Tunable parameters for the mean reversion strategy."""

    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    adx_period: int = 14
    adx_max: float = 25.0
    atr_period: int = 14
    stop_loss_atr_mult: float = 1.5
    min_conviction: float = 0.5
    cooldown_bars: int = 10
    # Phase 2 fields:
    trend_reject_threshold: float = 0.6
    extended_deviation_threshold: float = 0.5
    portfolio_a_min_conviction: float = 0.65
    vol_lookback: int = 10
    # Phase 4 additions: funding rate boost
    funding_rate_boost: float = 0.08
    funding_z_score_threshold: float = 1.5
    funding_min_samples: int = 10


class MeanReversionStrategy(SignalStrategy):
    """Bollinger Band mean reversion with RSI confirmation, multi-factor trend
    filter, adaptive band width, extended targets, and Portfolio A routing.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: MeanReversionParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or MeanReversionParams()

        if config:
            p = config.get("parameters", {})
            self._params = MeanReversionParams(
                bb_period=p.get("bb_period", self._params.bb_period),
                bb_std=p.get("bb_std", self._params.bb_std),
                rsi_period=p.get("rsi_period", self._params.rsi_period),
                rsi_overbought=p.get("rsi_overbought", self._params.rsi_overbought),
                rsi_oversold=p.get("rsi_oversold", self._params.rsi_oversold),
                adx_period=p.get("adx_period", self._params.adx_period),
                adx_max=p.get("adx_max", self._params.adx_max),
                atr_period=p.get("atr_period", self._params.atr_period),
                stop_loss_atr_mult=p.get(
                    "stop_loss_atr_mult", self._params.stop_loss_atr_mult,
                ),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                trend_reject_threshold=p.get(
                    "trend_reject_threshold", self._params.trend_reject_threshold,
                ),
                extended_deviation_threshold=p.get(
                    "extended_deviation_threshold",
                    self._params.extended_deviation_threshold,
                ),
                portfolio_a_min_conviction=p.get(
                    "portfolio_a_min_conviction", self._params.portfolio_a_min_conviction,
                ),
                vol_lookback=p.get("vol_lookback", self._params.vol_lookback),
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
        self._bars_since_signal = self._params.cooldown_bars

    @property
    def name(self) -> str:
        return "mean_reversion"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return self._params.bb_period + self._params.adx_period + 5

    def _compute_trend_strength(
        self,
        closes: NDArray[np.float64],
        cur_atr: float,
        cur_adx: float,
        adx_valid: bool,
        ema_period: int = 20,
        lookback: int = 5,
    ) -> float:
        """Compute composite trend strength from EMA slope, consecutive closes, and ADX.

        Returns a value in [0, 1] where higher means stronger trend.
        """
        ema_vals = ema(closes, ema_period)
        if cur_atr > 0 and not np.isnan(ema_vals[-1]) and not np.isnan(ema_vals[-2]):
            ema_slope = (ema_vals[-1] - ema_vals[-2]) / cur_atr
        else:
            ema_slope = 0.0
        slope_score = min(abs(ema_slope) / 0.5, 1.0) * 0.4  # 0-0.4

        consecutive = 0
        for i in range(len(closes) - 1, max(len(closes) - lookback - 1, 0), -1):
            if i < 1:
                break
            if (closes[i] > closes[i - 1]) == (closes[-1] > closes[-2]):
                consecutive += 1
            else:
                break
        consec_score = min(consecutive / lookback, 1.0) * 0.3  # 0-0.3

        adx_score = min(cur_adx / 50.0, 1.0) * 0.3 if adx_valid else 0.15  # 0-0.3
        return slope_score + consec_score + adx_score

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate Bollinger Band breach with RSI, trend, and volume filters."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        closes = store.closes
        highs = store.highs
        lows = store.lows

        # Compute ATR for adaptive bands and stop-loss
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]

        if np.isnan(cur_atr):
            return []

        # Adaptive band width based on ATR percentile (MR-02)
        adaptive_result = compute_adaptive_threshold(
            atr_vals, cur_atr, p.bb_std,
            low_vol_mult=0.85, high_vol_mult=1.15, min_samples=20,
        )
        adaptive_std = adaptive_result.adjusted_threshold

        bb = bollinger_bands(closes, p.bb_period, adaptive_std)
        rsi_vals = rsi(closes, p.rsi_period)
        adx_vals = adx(highs, lows, closes, p.adx_period)

        cur_close = closes[-1]
        cur_upper = bb.upper[-1]
        cur_lower = bb.lower[-1]
        cur_middle = bb.middle[-1]
        cur_rsi = rsi_vals[-1]
        cur_adx = adx_vals[-1]

        if any(np.isnan(v) for v in [cur_upper, cur_lower, cur_middle]):
            return []

        # Multi-factor trend rejection (MR-01)
        adx_valid = not np.isnan(cur_adx)
        trend_strength = self._compute_trend_strength(closes, cur_atr, cur_adx, adx_valid)
        if trend_strength > p.trend_reject_threshold:
            return []

        # Debug: log indicator values periodically
        if self._bars_since_signal % 10 == 0:
            _log.info(
                "mr_indicators",
                samples=store.sample_count,
                close=round(cur_close, 2),
                bb_upper=round(cur_upper, 2),
                bb_lower=round(cur_lower, 2),
                bb_mid=round(cur_middle, 2),
                adx=round(cur_adx, 2) if adx_valid else "NaN",
                rsi=round(cur_rsi, 2),
                atr=round(cur_atr, 4),
                adaptive_std=round(adaptive_std, 4),
                trend_strength=round(trend_strength, 3),
            )

        # Detect band breach
        below_lower = cur_close < cur_lower
        above_upper = cur_close > cur_upper

        if not below_lower and not above_upper:
            return []

        # RSI confirmation
        rsi_valid = not np.isnan(cur_rsi)
        if below_lower and rsi_valid and cur_rsi > p.rsi_oversold:
            return []  # Price below band but RSI not oversold -- skip
        if above_upper and rsi_valid and cur_rsi < p.rsi_overbought:
            return []  # Price above band but RSI not overbought -- skip

        # Compute band deviation
        band_width = cur_upper - cur_lower
        if band_width <= 0:
            return []

        if below_lower:
            deviation = (cur_lower - cur_close) / band_width
        else:
            deviation = (cur_close - cur_upper) / band_width

        # Volume conviction boost (D-15)
        bar_vols = store.bar_volumes
        volume_ratio = 1.0
        if len(bar_vols) >= p.vol_lookback:
            recent_vols = np.abs(bar_vols[-p.vol_lookback:])
            vol_avg = np.mean(recent_vols)
            cur_vol = np.abs(bar_vols[-1])
            volume_ratio = cur_vol / vol_avg if vol_avg > 0 else 1.0

        # Compute conviction (3-component with volume)
        conviction = self._compute_conviction(
            deviation,
            cur_rsi if rsi_valid else 50.0,
            below_lower,
            volume_ratio,
        )

        # Funding rate boost (Phase 4)
        direction = PositionSide.LONG if below_lower else PositionSide.SHORT
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

        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))
        middle_d = Decimal(str(cur_middle))
        upper_d = Decimal(str(cur_upper))
        lower_d = Decimal(str(cur_lower))

        # Extended take-profit targets (MR-03, D-09, D-10, D-11)
        if deviation > p.extended_deviation_threshold:
            # Strong reversion -- extended target
            if direction == PositionSide.LONG:
                extended_target = round_to_tick(
                    middle_d + (middle_d - lower_d) * Decimal("0.5")
                )
            else:
                extended_target = round_to_tick(
                    middle_d - (upper_d - middle_d) * Decimal("0.5")
                )
            take_profit = extended_target
            partial_target = round_to_tick(middle_d)
        else:
            take_profit = round_to_tick(middle_d)
            partial_target = None

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)))
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)))

        # Portfolio A routing (MR-04, D-01, D-03)
        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        reasoning = (
            f"BB mean reversion {'long' if below_lower else 'short'}: "
            f"price={cur_close:.2f}, "
            f"{'lower' if below_lower else 'upper'}="
            f"{cur_lower if below_lower else cur_upper:.2f}, "
            f"middle={cur_middle:.2f}"
            + (f", RSI={cur_rsi:.1f}" if rsi_valid else "")
            + (f", ADX={cur_adx:.1f}" if adx_valid else "")
            + f", trend={trend_strength:.2f}"
        )

        metadata: dict[str, object] = {
            "bb_upper": round(cur_upper, 2),
            "bb_lower": round(cur_lower, 2),
            "bb_middle": round(cur_middle, 2),
            "deviation": round(deviation, 4),
            "rsi": round(cur_rsi, 1) if rsi_valid else None,
            "adx": round(cur_adx, 1) if adx_valid else None,
            "atr": round(cur_atr, 2),
            "volume_ratio": round(volume_ratio, 3),
            "adaptive_std": round(adaptive_std, 4),
            "trend_strength": round(trend_strength, 3),
            "partial_target": str(partial_target) if partial_target else None,
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
            source=SignalSource.MEAN_REVERSION,
            time_horizon=timedelta(hours=8),
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
        deviation: float,
        rsi_value: float,
        is_long: bool,
        volume_ratio: float = 1.0,
    ) -> float:
        """Compute conviction from band deviation, RSI extremity, and volume.

        Deviation component (0-0.40): how far beyond the band.
        RSI component (0-0.35): how extreme the RSI reading is.
        Volume component (0-0.25): high volume on band touch = confirmation (D-15).
        """
        dev_score = min(deviation / 1.0, 0.40)
        dev_score = max(dev_score, 0.0)

        if is_long:
            # Lower RSI = more oversold = higher conviction for long
            rsi_score = max(0.0, min((30.0 - rsi_value + 20.0) / 80.0, 0.35))
        else:
            # Higher RSI = more overbought = higher conviction for short
            rsi_score = max(0.0, min((rsi_value - 70.0 + 20.0) / 80.0, 0.35))

        # Volume component: high volume on band touch = confirmation
        vol_score = min(max((volume_ratio - 0.5) / 3.0, 0.0), 0.25)

        return round(min(dev_score + rsi_score + vol_score, 1.0), 3)
