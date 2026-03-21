"""Momentum strategy — multi-timeframe EMA crossover with ADX trend filter.

Signal logic:
  1. Compute fast EMA and slow EMA on the price buffer.
  2. Detect crossovers (fast crosses above slow → bullish, below → bearish).
  3. Require ADX > threshold to confirm a trending market.
  4. RSI confirmation: reject long if overbought, reject short if oversold.
  5. Conviction scales with ADX strength and RSI agreement.
  6. Stop-loss placed at 2x ATR from entry; take-profit at 3x ATR.
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
from libs.indicators.moving_averages import ema
from libs.indicators.oscillators import adx, rsi
from libs.indicators.volatility import atr

from libs.common.logging import setup_logging

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy

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


class MomentumStrategy(SignalStrategy):
    """Multi-timeframe EMA crossover with ADX filter and RSI confirmation.

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
                rsi_period=p.get("rsi_period", self._params.rsi_period),
                rsi_overbought=p.get("rsi_overbought", self._params.rsi_overbought),
                rsi_oversold=p.get("rsi_oversold", self._params.rsi_oversold),
                atr_period=p.get("atr_period", self._params.atr_period),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
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
        """Evaluate EMA crossover, ADX filter, and RSI confirmation."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
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

        # RSI confirmation
        rsi_valid = not np.isnan(cur_rsi)
        if bullish_cross and rsi_valid and cur_rsi > p.rsi_overbought:
            return []  # Don't go long when overbought
        if bearish_cross and rsi_valid and cur_rsi < p.rsi_oversold:
            return []  # Don't go short when oversold

        # Compute conviction
        conviction = self._compute_conviction(
            cur_adx if adx_valid else 25.0,
            cur_rsi if rsi_valid else 50.0,
            bullish_cross,
        )

        if conviction < p.min_conviction:
            return []

        # Build signal
        direction = PositionSide.LONG if bullish_cross else PositionSide.SHORT
        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)))
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)))

        reasoning = (
            f"EMA crossover {'bullish' if bullish_cross else 'bearish'}: "
            f"fast({p.fast_ema_period})={cur_fast:.2f} vs slow({p.slow_ema_period})={cur_slow:.2f}, "
            f"ADX={cur_adx:.1f}" + (f", RSI={cur_rsi:.1f}" if rsi_valid else "")
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=INSTRUMENT_ID,
            direction=direction,
            conviction=conviction,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=4),
            reasoning=reasoning,
            suggested_target=PortfolioTarget.B,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "fast_ema": round(cur_fast, 2),
                "slow_ema": round(cur_slow, 2),
                "adx": round(cur_adx, 1) if adx_valid else None,
                "rsi": round(cur_rsi, 1) if rsi_valid else None,
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    def _compute_conviction(
        self,
        adx_value: float,
        rsi_value: float,
        is_bullish: bool,
    ) -> float:
        """Compute conviction from ADX strength and RSI agreement.

        ADX contribution (0-0.5): scales from 0 at ADX=20 to 0.5 at ADX=50+.
        RSI contribution (0-0.5): scales based on how much RSI agrees with direction.
        """
        # ADX component: stronger trend = higher conviction
        adx_score = min((adx_value - 20.0) / 60.0, 0.5)
        adx_score = max(adx_score, 0.0)

        # RSI component: RSI alignment with direction
        if is_bullish:
            # For bullish: RSI 30-50 is ideal (not overbought, room to run)
            rsi_score = max(0.0, min((70.0 - rsi_value) / 80.0, 0.5))
        else:
            # For bearish: RSI 50-70 is ideal
            rsi_score = max(0.0, min((rsi_value - 30.0) / 80.0, 0.5))

        return round(min(adx_score + rsi_score, 1.0), 3)
