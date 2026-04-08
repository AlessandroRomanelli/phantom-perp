"""VWAP Deviation Strategy -- mean reversion signals from session VWAP.

Signal logic:
  1. Compute session-aware VWAP using volume-weighted price average.
  2. Price significantly below session VWAP -> LONG (revert to VWAP).
  3. Price significantly above session VWAP -> SHORT (revert to VWAP).
  4. Session time awareness: signals later in session have higher conviction
     because VWAP has stabilized with more data (VWAP-04).
  5. Early session suppression: first min_session_progress fraction of session
     is suppressed since VWAP is unreliable with few bars.
  6. Supports two VWAP computation modes:
     a. Session reset with clamped bar_volumes (use_session_reset=True)
     b. Rolling window with 24h volumes as weights (use_session_reset=False, D-07)

Feasibility validation (VWAP-01):
  - bar_volumes (np.diff of 24h rolling volume) have ~48% negative values.
  - Clamping negatives to 0 produces a stable VWAP anchor (std 0.17 vs price 1.32).
  - Alternative rolling approach using volumes (always positive) also works.
  - Both approaches produce VWAP that is significantly smoother than raw price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray

from libs.common.instruments import get_instrument
from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy


@dataclass
class VWAPParams:
    """Tunable parameters for the VWAP deviation strategy."""

    session_reset_hour_utc: int = 0       # 00:00 UTC for crypto (VWAP-02)
    deviation_threshold: float = 2.0      # Std deviations from VWAP to trigger (VWAP-03)
    min_session_progress: float = 0.2     # Don't signal in first 20% of session (VWAP-04)
    session_conviction_weight: float = 0.3  # How much session progress affects conviction
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 1.5     # Tighter TP since targeting mean reversion to VWAP
    min_conviction: float = 0.40
    cooldown_bars: int = 10
    route_a_min_conviction: float = 0.70
    lookback_bars: int = 60               # Rolling window for VWAP if no session reset
    use_session_reset: bool = True        # False uses alternative rolling approach (D-07)
    session_length_hours: int = 24        # Session length for progress computation
    enabled: bool = True


class VWAPStrategy(SignalStrategy):
    """VWAP deviation mean reversion strategy with session time awareness.

    Computes session-aware VWAP and emits mean reversion signals when price
    deviates significantly. Conviction scales with both deviation magnitude
    and session progress (later in session = more reliable VWAP).

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: VWAPParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or VWAPParams()

        if config:
            p = config.get("parameters", {})
            self._params = VWAPParams(
                session_reset_hour_utc=p.get(
                    "session_reset_hour_utc", self._params.session_reset_hour_utc,
                ),
                deviation_threshold=p.get(
                    "deviation_threshold", self._params.deviation_threshold,
                ),
                min_session_progress=p.get(
                    "min_session_progress", self._params.min_session_progress,
                ),
                session_conviction_weight=p.get(
                    "session_conviction_weight", self._params.session_conviction_weight,
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
                route_a_min_conviction=p.get(
                    "route_a_min_conviction", self._params.route_a_min_conviction,
                ),
                lookback_bars=p.get("lookback_bars", self._params.lookback_bars),
                use_session_reset=p.get(
                    "use_session_reset", self._params.use_session_reset,
                ),
                session_length_hours=p.get(
                    "session_length_hours", self._params.session_length_hours,
                ),
                enabled=p.get("enabled", self._params.enabled),
            )

        self._enabled = self._params.enabled
        self._bars_since_signal = self._params.cooldown_bars

    @property
    def name(self) -> str:
        return "vwap"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return max(self._params.atr_period + 5, 20)

    def _find_session_start_index(
        self,
        timestamps: NDArray[np.float64],
        reset_hour: int,
    ) -> int:
        """Find the index of the first bar after the most recent session reset.

        Args:
            timestamps: Unix epoch timestamps of all bars.
            reset_hour: UTC hour at which session resets.

        Returns:
            Index into timestamps of first bar in current session.
        """
        if len(timestamps) == 0:
            return 0

        # Find the most recent reset boundary
        last_ts = datetime.fromtimestamp(timestamps[-1], tz=UTC)
        reset_today = last_ts.replace(
            hour=reset_hour, minute=0, second=0, microsecond=0,
        )

        if last_ts < reset_today:
            # Reset hasn't happened today, use yesterday's
            reset_today -= timedelta(days=1)

        reset_epoch = reset_today.timestamp()

        # Find first bar at or after reset
        indices = np.where(timestamps >= reset_epoch)[0]
        if len(indices) == 0:
            return 0
        return int(indices[0])

    def _compute_session_progress(
        self,
        timestamps: NDArray[np.float64],
        session_start_idx: int,
        session_length_hours: int,
    ) -> float:
        """Compute how far through the current session we are.

        Returns a value in [0, 1] where 0 = session just started,
        1 = session nearly complete.
        """
        if len(timestamps) == 0 or session_start_idx >= len(timestamps):
            return 0.0

        session_start = timestamps[session_start_idx]
        current = timestamps[-1]
        elapsed = current - session_start
        session_length = session_length_hours * 3600.0

        if session_length <= 0:
            return 0.0

        return min(max(elapsed / session_length, 0.0), 1.0)

    def _compute_vwap(
        self,
        closes: NDArray[np.float64],
        weights: NDArray[np.float64],
        start_idx: int,
    ) -> float | None:
        """Compute VWAP from start_idx to end using given weights.

        Args:
            closes: Price series.
            weights: Volume weights (clamped bar_volumes or 24h volumes).
            start_idx: First index to include in VWAP computation.

        Returns:
            VWAP value, or None if insufficient data.
        """
        session_closes = closes[start_idx:]
        session_weights = weights[start_idx:]

        if len(session_closes) < 2:
            return None

        # Clamp weights to non-negative
        clamped = np.maximum(session_weights, 0.0)
        total_weight = np.sum(clamped)

        if total_weight <= 0:
            # Fallback to simple average if all weights are zero
            return float(np.mean(session_closes))

        return float(np.sum(session_closes * clamped) / total_weight)

    def _compute_vwap_rolling(
        self,
        closes: NDArray[np.float64],
        volumes: NDArray[np.float64],
        lookback: int,
    ) -> float | None:
        """Compute rolling VWAP using 24h volumes as weights (D-07).

        Args:
            closes: Price series.
            volumes: 24h rolling volumes (always positive).
            lookback: Number of bars to include.

        Returns:
            VWAP value, or None if insufficient data.
        """
        if len(closes) < lookback or len(volumes) < lookback:
            return None

        window_p = closes[-lookback:]
        window_v = volumes[-lookback:]

        total_v = np.sum(window_v)
        if total_v <= 0:
            return float(np.mean(window_p))

        return float(np.sum(window_p * window_v) / total_v)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate VWAP deviation and emit mean reversion signals."""
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
        timestamps = store.timestamps
        volumes = store.volumes

        # Compute VWAP based on mode
        if p.use_session_reset:
            session_start_idx = self._find_session_start_index(
                timestamps, p.session_reset_hour_utc,
            )
            # Use bar_volumes (clamped) for session VWAP.
            # bar_volumes now has the same length as closes (one entry per sample).
            bar_vols = store.bar_volumes
            if len(bar_vols) == 0:
                return []

            bv_start = session_start_idx
            if bv_start >= len(bar_vols):
                return []

            vwap = self._compute_vwap(closes, bar_vols, bv_start)

            # Session progress
            session_progress = self._compute_session_progress(
                timestamps, session_start_idx, p.session_length_hours,
            )
        else:
            # Alternative rolling approach (D-07)
            vwap = self._compute_vwap_rolling(closes, volumes, p.lookback_bars)
            # No session concept for rolling -- use 0.5 as neutral progress
            session_progress = 0.5
            session_start_idx = max(0, len(closes) - p.lookback_bars)

        if vwap is None:
            return []

        # Early session suppression (VWAP-04)
        if p.use_session_reset and session_progress < p.min_session_progress:
            return []

        cur_price = float(snapshot.last_price)

        # Compute deviation in standard deviations
        if p.use_session_reset:
            session_prices = closes[session_start_idx:]
        else:
            session_prices = closes[-p.lookback_bars:]

        if len(session_prices) < 5:
            return []

        price_std = float(np.std(session_prices, ddof=1))
        if price_std < 1e-10:
            return []

        deviation = (cur_price - vwap) / price_std

        # Check if deviation exceeds threshold
        if abs(deviation) < p.deviation_threshold:
            return []

        # Direction: LONG if price below VWAP, SHORT if above
        if deviation < -p.deviation_threshold:
            direction = PositionSide.LONG
        else:
            direction = PositionSide.SHORT

        # Compute conviction (2-component + base)
        conviction = self._compute_conviction(
            deviation, p.deviation_threshold, session_progress, p,
        )

        if conviction < p.min_conviction:
            return []

        # ATR for stops
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry + atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(p.stop_loss_atr_mult)), tick_size)
            take_profit = round_to_tick(entry - atr_d * Decimal(str(p.take_profit_atr_mult)), tick_size)

        # Route A routing
        suggested_route = (
            Route.A
            if conviction >= p.route_a_min_conviction
            else Route.B
        )

        reasoning = (
            f"VWAP {'long' if direction == PositionSide.LONG else 'short'}: "
            f"price={cur_price:.2f}, vwap={vwap:.2f}, "
            f"deviation={deviation:+.2f} std, "
            f"session={session_progress:.0%}"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.VWAP,
            time_horizon=timedelta(hours=4),
            reasoning=reasoning,
            suggested_route=suggested_route,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "vwap": round(vwap, 2),
                "deviation": round(deviation, 4),
                "session_progress": round(session_progress, 3),
                "atr": round(cur_atr, 2),
                "price_std": round(price_std, 4),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_conviction(
        deviation: float,
        threshold: float,
        session_progress: float,
        params: VWAPParams,
    ) -> float:
        """Compute conviction from deviation magnitude and session progress.

        Deviation component (0-0.60): scales with how far beyond threshold.
        Session progress component (0-0.30): later in session = more reliable VWAP.
        Base = 0.10.
        """
        # Deviation component: 0-0.60
        excess = abs(deviation) - threshold
        dev_score = min(excess / 3.0, 0.60)
        dev_score = max(dev_score, 0.0)

        # Session progress component: 0-0.30 (VWAP-04)
        session_score = session_progress * params.session_conviction_weight

        base = 0.10

        return round(min(base + dev_score + session_score, 1.0), 3)
