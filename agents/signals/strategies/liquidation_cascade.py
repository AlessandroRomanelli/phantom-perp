"""Liquidation cascade strategy — fade or follow forced liquidation events.

Signal logic:
  1. Track rolling open interest change rate over a short window.
  2. Detect OI drop rate exceeding threshold (mass liquidation event).
  3. Combine with volatility spike and extreme orderbook imbalance.
  4. Fade mode: after sharp OI drop + price dump -> LONG (expect bounce).
  5. Follow mode: during accelerating OI drop + extreme imbalance -> SHORT.
  6. Conviction scales with OI drop rate, volatility spike, imbalance.
  7. Short time horizon (<=2h) -> routes to Portfolio A (autonomous).
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
from libs.indicators.volatility import atr

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy


@dataclass
class LiquidationCascadeParams:
    """Tunable parameters for the liquidation cascade strategy."""

    oi_lookback: int = 10
    oi_drop_threshold_pct: float = 2.0
    imbalance_threshold: float = 0.3
    vol_spike_mult: float = 1.5
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 2.5
    min_conviction: float = 0.55
    cooldown_bars: int = 15


class LiquidationCascadeStrategy(SignalStrategy):
    """Detects liquidation cascades via OI drops and fades or follows them.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: LiquidationCascadeParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or LiquidationCascadeParams()

        if config:
            p = config.get("parameters", {})
            self._params = LiquidationCascadeParams(
                oi_lookback=p.get("oi_lookback", self._params.oi_lookback),
                oi_drop_threshold_pct=p.get(
                    "oi_drop_threshold_pct", self._params.oi_drop_threshold_pct,
                ),
                imbalance_threshold=p.get(
                    "imbalance_threshold", self._params.imbalance_threshold,
                ),
                vol_spike_mult=p.get("vol_spike_mult", self._params.vol_spike_mult),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
            )

        self._enabled = True
        self._bars_since_signal = self._params.cooldown_bars

    @property
    def name(self) -> str:
        return "liquidation_cascade"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return max(self._params.oi_lookback, self._params.atr_period) + 5

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Detect liquidation cascade and signal fade or follow."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        closes = store.closes
        highs = store.highs
        lows = store.lows
        ois = store.open_interests

        # OI change rate over lookback window
        oi_change_pct = self._compute_oi_change_pct(ois, p.oi_lookback)
        if oi_change_pct is None:
            return []

        # Not a liquidation event if OI hasn't dropped enough
        if oi_change_pct > -p.oi_drop_threshold_pct:
            return []

        # Price change over same window
        price_change_pct = self._compute_price_change_pct(closes, p.oi_lookback)
        if price_change_pct is None:
            return []

        # Orderbook imbalance
        cur_imbalance = snapshot.orderbook_imbalance

        # Volatility check
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        # Determine direction: fade or follow
        direction, mode = self._determine_direction(
            oi_change_pct, price_change_pct, cur_imbalance, p,
        )
        if direction is None:
            return []

        conviction = self._compute_conviction(
            oi_change_pct, price_change_pct, cur_imbalance, snapshot.volatility_1h, p,
        )
        if conviction < p.min_conviction:
            return []

        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)))
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)))
            take_profit = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)))

        reasoning = (
            f"Liquidation cascade {mode}: OI dropped {oi_change_pct:.2f}% "
            f"over {p.oi_lookback} bars, price {price_change_pct:+.2f}%, "
            f"imbalance={cur_imbalance:+.2f}"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=INSTRUMENT_ID,
            direction=direction,
            conviction=conviction,
            source=SignalSource.LIQUIDATION_CASCADE,
            time_horizon=timedelta(hours=2),
            reasoning=reasoning,
            suggested_target=PortfolioTarget.A,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "oi_change_pct": round(oi_change_pct, 3),
                "price_change_pct": round(price_change_pct, 3),
                "orderbook_imbalance": round(cur_imbalance, 3),
                "mode": mode,
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_oi_change_pct(
        ois: np.ndarray, lookback: int,
    ) -> float | None:
        """Compute percentage OI change over lookback window."""
        if len(ois) < lookback + 1:
            return None
        old_oi = ois[-(lookback + 1)]
        cur_oi = ois[-1]
        if old_oi <= 0:
            return None
        return ((cur_oi - old_oi) / old_oi) * 100.0

    @staticmethod
    def _compute_price_change_pct(
        closes: np.ndarray, lookback: int,
    ) -> float | None:
        """Compute percentage price change over lookback window."""
        if len(closes) < lookback + 1:
            return None
        old = closes[-(lookback + 1)]
        cur = closes[-1]
        if old <= 0:
            return None
        return ((cur - old) / old) * 100.0

    @staticmethod
    def _determine_direction(
        oi_change_pct: float,
        price_change_pct: float,
        imbalance: float,
        params: LiquidationCascadeParams,
    ) -> tuple[PositionSide | None, str]:
        """Decide whether to fade or follow the cascade.

        Fade: OI drops and price already dumped -> expect bounce (LONG).
               OI drops and price already pumped -> expect pullback (SHORT).
        Follow: extreme imbalance suggests cascade is accelerating.
        """
        # Fade: OI dropped, price dumped -> long the bounce
        if price_change_pct < -1.0 and imbalance < -params.imbalance_threshold:
            return PositionSide.LONG, "fade"

        # Fade: OI dropped, price pumped -> short squeeze exhaustion
        if price_change_pct > 1.0 and imbalance > params.imbalance_threshold:
            return PositionSide.SHORT, "fade"

        # Follow: accelerating cascade with heavy sell imbalance
        if oi_change_pct < -params.oi_drop_threshold_pct * 2 and imbalance < -0.5:
            return PositionSide.SHORT, "follow"

        return None, ""

    @staticmethod
    def _compute_conviction(
        oi_change_pct: float,
        price_change_pct: float,
        imbalance: float,
        volatility_1h: float,
        params: LiquidationCascadeParams,
    ) -> float:
        """Compute conviction from cascade strength indicators.

        OI drop component (0-0.4): how severe the OI drop is.
        Imbalance component (0-0.3): how extreme the orderbook is.
        Volatility component (0-0.3): elevated vol confirms cascade.
        """
        # OI component: bigger drop = higher conviction
        oi_score = min(abs(oi_change_pct) / 10.0, 0.4)

        # Imbalance component
        imb_score = min(abs(imbalance) / 1.0, 0.3)

        # Volatility component: higher vol = more confidence it's a real event
        vol_score = min(volatility_1h / 1.0, 0.3)

        return round(min(oi_score + imb_score + vol_score, 1.0), 3)
