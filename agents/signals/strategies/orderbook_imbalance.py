"""Orderbook imbalance (OBI) strategy -- short-horizon directional signals.

Signal logic:
  1. Compute time-weighted average of orderbook imbalance over lookback window.
  2. Linear weights: recent bars weighted more heavily than older bars.
  3. Depth gate: suppress signals when spread is too wide (thin orderbook).
  4. 3-component conviction: imbalance magnitude + spread quality + volume.
  5. Portfolio A routing for high-conviction signals (OBI-04).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from libs.common.instruments import get_instrument
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy


@dataclass
class OrderbookImbalanceParams:
    """Tunable parameters for the orderbook imbalance strategy."""

    lookback_bars: int = 10           # 10 bars * 60s = 10 min window (OBI-02)
    imbalance_threshold: float = 0.25  # Minimum TWA imbalance to trigger
    max_spread_bps: float = 20.0       # Suppress if spread > 20 bps (OBI-03)
    atr_period: int = 14
    stop_loss_atr_mult: float = 1.5    # Tight stops for short horizon
    take_profit_atr_mult: float = 2.0
    min_conviction: float = 0.45       # Higher bar due to noisy data (D-10)
    cooldown_bars: int = 3             # Short cooldown for frequent firing (D-09)
    portfolio_a_min_conviction: float = 0.65  # Portfolio A threshold (OBI-04)
    enabled: bool = True


class OrderbookImbalanceStrategy(SignalStrategy):
    """Bid/ask depth imbalance strategy for short-horizon directional signals.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: OrderbookImbalanceParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or OrderbookImbalanceParams()

        if config:
            p = config.get("parameters", {})
            self._params = OrderbookImbalanceParams(
                lookback_bars=p.get("lookback_bars", self._params.lookback_bars),
                imbalance_threshold=p.get(
                    "imbalance_threshold", self._params.imbalance_threshold,
                ),
                max_spread_bps=p.get("max_spread_bps", self._params.max_spread_bps),
                atr_period=p.get("atr_period", self._params.atr_period),
                stop_loss_atr_mult=p.get(
                    "stop_loss_atr_mult", self._params.stop_loss_atr_mult,
                ),
                take_profit_atr_mult=p.get(
                    "take_profit_atr_mult", self._params.take_profit_atr_mult,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
                portfolio_a_min_conviction=p.get(
                    "portfolio_a_min_conviction",
                    self._params.portfolio_a_min_conviction,
                ),
                enabled=p.get("enabled", self._params.enabled),
            )

        self._enabled = self._params.enabled
        self._bars_since_signal = self._params.cooldown_bars  # Start ready

    @property
    def name(self) -> str:
        return "orderbook_imbalance"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return self._params.lookback_bars + self._params.atr_period

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate time-weighted orderbook imbalance for directional signals."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        tick_size = get_instrument(snapshot.instrument).tick_size
        imbalances = store.orderbook_imbalances
        closes = store.closes
        highs = store.highs
        lows = store.lows
        volumes = store.volumes

        # Time-weighted imbalance over lookback window (OBI-02)
        window = imbalances[-p.lookback_bars:]
        weights = np.arange(1, len(window) + 1, dtype=np.float64)
        tw_imbalance = float(np.average(window, weights=weights))

        # Depth gate: suppress on wide spread (OBI-03)
        if snapshot.spread_bps > p.max_spread_bps:
            return []

        # Threshold check
        if abs(tw_imbalance) < p.imbalance_threshold:
            return []

        # Direction
        direction = PositionSide.LONG if tw_imbalance > 0 else PositionSide.SHORT

        # ATR for stops
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        # 3-component conviction model
        # Imbalance magnitude (0-0.45)
        imb_excess = abs(tw_imbalance) - p.imbalance_threshold
        if p.imbalance_threshold > 0:
            imb_score = min(max(imb_excess / p.imbalance_threshold * 0.45, 0.0), 0.45)
        else:
            # Threshold disabled; scale by absolute imbalance
            imb_score = min(max(abs(tw_imbalance) * 0.45, 0.0), 0.45)

        # Spread quality (0-0.30)
        spread_score = max(0.0, min((20.0 - snapshot.spread_bps) / 20.0 * 0.30, 0.30))

        # Volume component (0-0.25)
        vol_window = volumes[-p.lookback_bars:]
        vol_mean = float(np.mean(vol_window))
        cur_vol = float(volumes[-1])
        volume_ratio = cur_vol / vol_mean if vol_mean > 0 else 1.0
        vol_score = min(max((volume_ratio - 0.5) / 2.0, 0.0), 0.25)

        conviction = round(min(imb_score + spread_score + vol_score, 1.0), 3)

        if conviction < p.min_conviction:
            return []

        # Portfolio routing (OBI-04)
        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        # Entry, stop, take profit
        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)

        reasoning = (
            f"OBI {'long' if direction == PositionSide.LONG else 'short'}: "
            f"tw_imbalance={tw_imbalance:+.3f}, spread={snapshot.spread_bps:.1f}bps"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.ORDERBOOK_IMBALANCE,
            time_horizon=timedelta(hours=1),
            reasoning=reasoning,
            suggested_target=suggested_target,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "tw_imbalance": round(tw_imbalance, 4),
                "spread_bps": round(snapshot.spread_bps, 2),
                "atr": round(cur_atr, 2),
                "volume_ratio": round(volume_ratio, 3),
                "imb_score": round(imb_score, 3),
                "spread_score": round(spread_score, 3),
                "vol_score": round(vol_score, 3),
            },
        )

        self._bars_since_signal = 0
        return [signal]
