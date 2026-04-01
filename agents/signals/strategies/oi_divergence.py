"""OI divergence strategy — detect price/open-interest mismatches and acceleration.

Signal logic:
  Mode 1 — Classic Divergence:
    Price up + OI down → SHORT (exhaustion: rally on falling participation)
    Price down + OI up → LONG (coiling: selloff but fresh longs absorbing)

  Mode 2 — OI Acceleration:
    Short-term OI ROC > long-term OI ROC (acceleration) → LONG (new demand building)
    Short-term OI ROC < long-term OI ROC (deceleration) → SHORT (unwinding)

  Combined conviction model (max 1.0):
    Divergence component (0–0.50): based on average price/OI move magnitude.
    Acceleration component (0–0.50): based on acceleration ratio vs threshold.
    Modes that agree sum their components; conflicting modes → no signal.

  ATR-based stops with per-instrument configurable multipliers.
  Route A routing for high-conviction signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np

from agents.signals.strategies.base import SignalStrategy
from libs.common.instruments import get_instrument
from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

if TYPE_CHECKING:
    from agents.signals.feature_store import FeatureStore
    from libs.common.models.market_snapshot import MarketSnapshot


@dataclass
class OIDivergenceParams:
    """Tunable parameters for the OI divergence strategy."""

    divergence_lookback: int = 20        # Bars for classic divergence window
    div_threshold_pct: float = 2.0       # Min % move in both price and OI to qualify
    accel_short_lookback: int = 5        # Short ROC window for acceleration mode
    accel_long_lookback: int = 20        # Long ROC window for acceleration mode
    accel_threshold: float = 2.0         # % ROC difference to trigger acceleration
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    min_conviction: float = 0.45
    cooldown_bars: int = 12
    max_holding_hours: int = 8
    route_a_min_conviction: float = 0.70
    enabled: bool = True


class OIDivergenceStrategy(SignalStrategy):
    """Detect price/OI mismatches and OI acceleration patterns.

    Two independent detection modes are combined with a conviction model.
    When both modes fire in the same direction their scores sum; when they
    disagree no signal is emitted. Route A receives signals at or above
    the high-conviction threshold; Route B receives the rest.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: OIDivergenceParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or OIDivergenceParams()

        if config:
            p = config.get("parameters", {})
            self._params = OIDivergenceParams(
                divergence_lookback=p.get(
                    "divergence_lookback", self._params.divergence_lookback,
                ),
                div_threshold_pct=p.get(
                    "div_threshold_pct", self._params.div_threshold_pct,
                ),
                accel_short_lookback=p.get(
                    "accel_short_lookback", self._params.accel_short_lookback,
                ),
                accel_long_lookback=p.get(
                    "accel_long_lookback", self._params.accel_long_lookback,
                ),
                accel_threshold=p.get(
                    "accel_threshold", self._params.accel_threshold,
                ),
                atr_period=p.get("atr_period", self._params.atr_period),
                stop_loss_atr_mult=p.get(
                    "stop_loss_atr_mult", self._params.stop_loss_atr_mult,
                ),
                take_profit_atr_mult=p.get(
                    "take_profit_atr_mult", self._params.take_profit_atr_mult,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
                max_holding_hours=p.get(
                    "max_holding_hours", self._params.max_holding_hours,
                ),
                route_a_min_conviction=p.get(
                    "route_a_min_conviction",
                    self._params.route_a_min_conviction,
                ),
                enabled=p.get("enabled", self._params.enabled),
            )

        self._enabled = self._params.enabled
        self._bars_since_signal = self._params.cooldown_bars  # Start ready

    @property
    def name(self) -> str:
        # Must match the YAML filename and strategy_matrix key for compatibility.
        return "oi_divergence"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return (
            max(self._params.divergence_lookback, self._params.accel_long_lookback)
            + self._params.atr_period
            + 5
        )

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate OI divergence and acceleration, emit directional signals."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        closes = store.closes
        ois = store.open_interests

        # Guard: need enough OI history for the longest lookback
        required = max(p.divergence_lookback, p.accel_long_lookback) + 1
        if len(ois) < required or len(closes) < required:
            return []

        # Guard: old OI must be positive to prevent division by zero
        old_oi_div = ois[-(p.divergence_lookback + 1)]
        old_oi_accel_long = ois[-(p.accel_long_lookback)]
        old_oi_accel_short = ois[-(p.accel_short_lookback)]

        if old_oi_div <= 0 or old_oi_accel_long <= 0 or old_oi_accel_short <= 0:
            return []

        # --- Mode 1: Classic Divergence ---
        price_pct = (closes[-1] - closes[-(p.divergence_lookback + 1)]) / closes[-(p.divergence_lookback + 1)] * 100.0
        oi_pct = (ois[-1] - old_oi_div) / old_oi_div * 100.0

        div_direction: PositionSide | None = None
        if price_pct > p.div_threshold_pct and oi_pct < -p.div_threshold_pct:
            # Price rising but OI falling → exhaustion → SHORT
            div_direction = PositionSide.SHORT
        elif price_pct < -p.div_threshold_pct and oi_pct > p.div_threshold_pct:
            # Price falling but OI rising → coiling → LONG
            div_direction = PositionSide.LONG

        # Divergence score: magnitude scaled to 0–0.50
        # Scale: threshold is the floor; 4× threshold maps to full score
        div_magnitude = (abs(price_pct) + abs(oi_pct)) / 2.0
        div_score = 0.0
        if div_direction is not None:
            div_excess = div_magnitude / p.div_threshold_pct  # ≥1.0 at threshold
            div_score = min(div_excess / 4.0 * 0.50 + 0.25, 0.50)

        # --- Mode 2: OI Acceleration ---
        roc_short = (ois[-1] - old_oi_accel_short) / old_oi_accel_short * 100.0
        roc_long = (ois[-1] - old_oi_accel_long) / old_oi_accel_long * 100.0
        acceleration = roc_short - roc_long

        accel_direction: PositionSide | None = None
        if acceleration > p.accel_threshold:
            # OI building faster short-term → LONG
            accel_direction = PositionSide.LONG
        elif acceleration < -p.accel_threshold:
            # OI unwinding faster short-term → SHORT
            accel_direction = PositionSide.SHORT

        # Acceleration score: scaled to 0–0.50 with gradual ramp
        # Scale: 1× threshold → 0.25, 3× threshold → 0.50
        accel_score = 0.0
        if accel_direction is not None:
            accel_ratio = abs(acceleration) / p.accel_threshold  # ≥1.0 at threshold
            accel_score = min(accel_ratio / 3.0 * 0.25 + 0.25, 0.50)

        # --- Combine modes ---
        if div_direction is None and accel_direction is None:
            # Neither mode fired
            return []

        if div_direction is not None and accel_direction is not None:
            if div_direction != accel_direction:
                # Modes conflict — no signal
                return []
            # Both agree — sum scores (capped at 1.0)
            direction = div_direction
            conviction = round(min(div_score + accel_score, 1.0), 3)
        elif div_direction is not None:
            direction = div_direction
            conviction = round(div_score, 3)
        else:
            direction = accel_direction  # type: ignore[assignment]
            conviction = round(accel_score, 3)

        if conviction < p.min_conviction:
            return []

        # --- ATR-based stops ---
        highs = store.highs
        lows = store.lows

        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        tick_size = get_instrument(snapshot.instrument).tick_size
        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(
                entry - atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size,
            )
            take_profit = round_to_tick(
                entry + atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size,
            )
        else:
            stop_loss = round_to_tick(
                entry + atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size,
            )
            take_profit = round_to_tick(
                entry - atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size,
            )

        # Portfolio routing: high conviction → A, else → B
        suggested_route = (
            Route.A
            if conviction >= p.route_a_min_conviction
            else Route.B
        )

        reasoning = (
            f"OI divergence {'long' if direction == PositionSide.LONG else 'short'}: "
            f"price_pct={price_pct:+.2f}%, oi_pct={oi_pct:+.2f}%, "
            f"accel={acceleration:+.2f}%, "
            f"div_score={div_score:.3f}, accel_score={accel_score:.3f}"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.OI_DIVERGENCE,
            time_horizon=timedelta(hours=p.max_holding_hours),
            reasoning=reasoning,
            suggested_route=suggested_route,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "price_pct": round(price_pct, 4),
                "oi_pct": round(oi_pct, 4),
                "acceleration": round(acceleration, 4),
                "div_score": round(div_score, 4),
                "accel_score": round(accel_score, 4),
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]
