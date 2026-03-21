"""Mean reversion strategy — Bollinger Band deviation with RSI confirmation.

Signal logic:
  1. Compute Bollinger Bands (20-period SMA +/- 2 std devs).
  2. Price below lower band -> LONG (expect reversion to mean).
  3. Price above upper band -> SHORT (expect reversion to mean).
  4. ADX filter: reject if ADX > threshold (strong trend kills reversion).
  5. RSI confirmation: require oversold for longs, overbought for shorts.
  6. Conviction scales with distance beyond band + RSI extremity.
  7. Stop-loss at 1.5x ATR beyond entry; take-profit at the middle band.
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
from libs.indicators.oscillators import adx, rsi
from libs.indicators.volatility import atr, bollinger_bands

from libs.common.logging import setup_logging

from agents.signals.feature_store import FeatureStore
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


class MeanReversionStrategy(SignalStrategy):
    """Bollinger Band mean reversion with RSI confirmation and ADX filter.

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
                min_conviction=p.get("min_conviction", self._params.min_conviction),
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

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate Bollinger Band breach with RSI and ADX filters."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        closes = store.closes
        highs = store.highs
        lows = store.lows

        bb = bollinger_bands(closes, p.bb_period, p.bb_std)
        rsi_vals = rsi(closes, p.rsi_period)
        adx_vals = adx(highs, lows, closes, p.adx_period)
        atr_vals = atr(highs, lows, closes, p.atr_period)

        cur_close = closes[-1]
        cur_upper = bb.upper[-1]
        cur_lower = bb.lower[-1]
        cur_middle = bb.middle[-1]
        cur_rsi = rsi_vals[-1]
        cur_adx = adx_vals[-1]
        cur_atr = atr_vals[-1]

        if any(np.isnan(v) for v in [cur_upper, cur_lower, cur_middle, cur_atr]):
            return []

        # ADX filter: skip if strong trend (reversion fails in trends)
        adx_valid = not np.isnan(cur_adx)

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
            )

        if adx_valid and cur_adx > p.adx_max:
            return []

        # Detect band breach
        below_lower = cur_close < cur_lower
        above_upper = cur_close > cur_upper

        if not below_lower and not above_upper:
            return []

        # RSI confirmation
        rsi_valid = not np.isnan(cur_rsi)
        if below_lower and rsi_valid and cur_rsi > p.rsi_oversold:
            return []  # Price below band but RSI not oversold — skip
        if above_upper and rsi_valid and cur_rsi < p.rsi_overbought:
            return []  # Price above band but RSI not overbought — skip

        # Compute conviction
        band_width = cur_upper - cur_lower
        if band_width <= 0:
            return []

        if below_lower:
            deviation = (cur_lower - cur_close) / band_width
        else:
            deviation = (cur_close - cur_upper) / band_width

        conviction = self._compute_conviction(
            deviation,
            cur_rsi if rsi_valid else 50.0,
            below_lower,
        )

        if conviction < p.min_conviction:
            return []

        direction = PositionSide.LONG if below_lower else PositionSide.SHORT
        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))
        middle_d = Decimal(str(cur_middle))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(middle_d)
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(middle_d)

        reasoning = (
            f"BB mean reversion {'long' if below_lower else 'short'}: "
            f"price={cur_close:.2f}, "
            f"{'lower' if below_lower else 'upper'}={cur_lower if below_lower else cur_upper:.2f}, "
            f"middle={cur_middle:.2f}"
            + (f", RSI={cur_rsi:.1f}" if rsi_valid else "")
            + (f", ADX={cur_adx:.1f}" if adx_valid else "")
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=INSTRUMENT_ID,
            direction=direction,
            conviction=conviction,
            source=SignalSource.MEAN_REVERSION,
            time_horizon=timedelta(hours=8),
            reasoning=reasoning,
            suggested_target=PortfolioTarget.B,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "bb_upper": round(cur_upper, 2),
                "bb_lower": round(cur_lower, 2),
                "bb_middle": round(cur_middle, 2),
                "deviation": round(deviation, 4),
                "rsi": round(cur_rsi, 1) if rsi_valid else None,
                "adx": round(cur_adx, 1) if adx_valid else None,
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_conviction(
        deviation: float,
        rsi_value: float,
        is_long: bool,
    ) -> float:
        """Compute conviction from band deviation and RSI extremity.

        Deviation component (0-0.5): how far beyond the band.
        RSI component (0-0.5): how extreme the RSI reading is.
        """
        dev_score = min(deviation / 1.0, 0.5)
        dev_score = max(dev_score, 0.0)

        if is_long:
            # Lower RSI = more oversold = higher conviction for long
            rsi_score = max(0.0, min((30.0 - rsi_value + 20.0) / 80.0, 0.5))
        else:
            # Higher RSI = more overbought = higher conviction for short
            rsi_score = max(0.0, min((rsi_value - 70.0 + 20.0) / 80.0, 0.5))

        return round(min(dev_score + rsi_score, 1.0), 3)
