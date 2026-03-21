"""Correlation strategy — basis divergence and OI/price divergence.

Signal logic:
  1. Track mark-index basis in basis points over time.
  2. Compute z-score of current basis vs rolling history.
  3. Extreme positive basis (mark >> index) -> mark overpriced -> SHORT.
  4. Extreme negative basis (mark << index) -> mark underpriced -> LONG.
  5. OI/price divergence: if OI drops while price rises -> bearish.
     If OI rises while price drops -> bullish (accumulation).
  6. Conviction scales with basis z-score + divergence strength.
  7. Medium time horizon (4-8h) -> routes to Portfolio B.
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
class CorrelationParams:
    """Tunable parameters for the correlation strategy."""

    basis_lookback: int = 60
    basis_zscore_threshold: float = 2.0
    oi_divergence_lookback: int = 20
    oi_divergence_threshold_pct: float = 1.5
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    min_conviction: float = 0.5
    cooldown_bars: int = 15


class CorrelationStrategy(SignalStrategy):
    """Basis divergence and OI/price divergence trading.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: CorrelationParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or CorrelationParams()

        if config:
            p = config.get("parameters", {})
            self._params = CorrelationParams(
                basis_lookback=p.get("basis_lookback", self._params.basis_lookback),
                basis_zscore_threshold=p.get(
                    "basis_zscore_threshold", self._params.basis_zscore_threshold,
                ),
                oi_divergence_lookback=p.get(
                    "oi_divergence_lookback", self._params.oi_divergence_lookback,
                ),
                oi_divergence_threshold_pct=p.get(
                    "oi_divergence_threshold_pct",
                    self._params.oi_divergence_threshold_pct,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
            )

        self._enabled = True
        self._bars_since_signal = self._params.cooldown_bars

    @property
    def name(self) -> str:
        return "correlation"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return max(
            self._params.basis_lookback,
            self._params.oi_divergence_lookback,
            self._params.atr_period,
        ) + 5

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate basis and OI/price divergence signals."""
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
        ois = store.open_interests

        # 1. Basis analysis (mark vs index)
        basis_bps = self._compute_basis_series(closes, index_prices)
        basis_zscore = self._compute_zscore(basis_bps[-1], basis_bps, p.basis_lookback)

        # 2. OI/price divergence
        oi_div = self._compute_oi_divergence(closes, ois, p.oi_divergence_lookback)

        # Need at least one signal trigger
        basis_trigger = abs(basis_zscore) >= p.basis_zscore_threshold
        div_trigger = oi_div is not None and abs(oi_div) >= p.oi_divergence_threshold_pct

        if not basis_trigger and not div_trigger:
            return []

        # Determine direction
        direction = self._determine_direction(basis_zscore, oi_div, basis_trigger, div_trigger)
        if direction is None:
            return []

        # ATR for stops
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        conviction = self._compute_conviction(basis_zscore, oi_div, basis_trigger, div_trigger)
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

        parts = []
        if basis_trigger:
            parts.append(f"basis z={basis_zscore:+.2f} ({basis_bps[-1]:.1f} bps)")
        if div_trigger and oi_div is not None:
            parts.append(f"OI/price divergence={oi_div:+.2f}%")

        reasoning = f"Correlation {'long' if direction == PositionSide.LONG else 'short'}: " + ", ".join(parts)

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=INSTRUMENT_ID,
            direction=direction,
            conviction=conviction,
            source=SignalSource.CORRELATION,
            time_horizon=timedelta(hours=6),
            reasoning=reasoning,
            suggested_target=PortfolioTarget.B,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "basis_bps": round(float(basis_bps[-1]), 2),
                "basis_zscore": round(basis_zscore, 3),
                "oi_divergence": round(oi_div, 3) if oi_div is not None else None,
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_basis_series(
        mark_prices: np.ndarray,
        index_prices: np.ndarray,
    ) -> np.ndarray:
        """Compute mark-index basis in basis points."""
        with np.errstate(divide="ignore", invalid="ignore"):
            basis = np.where(
                index_prices > 0,
                (mark_prices - index_prices) / index_prices * 10_000,
                0.0,
            )
        return basis

    @staticmethod
    def _compute_zscore(
        current: float,
        series: np.ndarray,
        lookback: int,
    ) -> float:
        """Compute z-score of current value vs rolling history."""
        window = series[-lookback:] if len(series) >= lookback else series
        if len(window) < 10:
            return 0.0
        mean = float(np.mean(window))
        std = float(np.std(window, ddof=1))
        if std < 1e-10:
            return 0.0
        return (current - mean) / std

    @staticmethod
    def _compute_oi_divergence(
        closes: np.ndarray,
        ois: np.ndarray,
        lookback: int,
    ) -> float | None:
        """Compute OI/price divergence over lookback window.

        Returns a divergence score:
          Positive = bullish divergence (OI rising, price falling -> accumulation)
          Negative = bearish divergence (OI falling, price rising -> distribution)
          None if insufficient data.
        """
        if len(closes) < lookback + 1 or len(ois) < lookback + 1:
            return None

        old_price = closes[-(lookback + 1)]
        cur_price = closes[-1]
        old_oi = ois[-(lookback + 1)]
        cur_oi = ois[-1]

        if old_price <= 0 or old_oi <= 0:
            return None

        price_pct = ((cur_price - old_price) / old_price) * 100.0
        oi_pct = ((cur_oi - old_oi) / old_oi) * 100.0

        # Divergence = OI direction vs price direction
        # If both move same direction: no divergence (return small value)
        # If opposite: divergence (sign indicates bullish/bearish)
        if price_pct > 0 and oi_pct < 0:
            # Price up, OI down → bearish divergence (distribution)
            return -(abs(price_pct) + abs(oi_pct)) / 2
        elif price_pct < 0 and oi_pct > 0:
            # Price down, OI up → bullish divergence (accumulation)
            return (abs(price_pct) + abs(oi_pct)) / 2
        else:
            return 0.0

    @staticmethod
    def _determine_direction(
        basis_zscore: float,
        oi_div: float | None,
        basis_trigger: bool,
        div_trigger: bool,
    ) -> PositionSide | None:
        """Determine trade direction from basis and divergence signals.

        When both trigger, they must agree. Conflicting signals -> no trade.
        """
        basis_dir: PositionSide | None = None
        div_dir: PositionSide | None = None

        if basis_trigger:
            # Extreme positive basis -> SHORT (mark overpriced)
            # Extreme negative basis -> LONG (mark underpriced)
            basis_dir = PositionSide.SHORT if basis_zscore > 0 else PositionSide.LONG

        if div_trigger and oi_div is not None:
            div_dir = PositionSide.LONG if oi_div > 0 else PositionSide.SHORT

        if basis_dir is not None and div_dir is not None:
            if basis_dir != div_dir:
                return None  # Conflicting signals
            return basis_dir

        return basis_dir or div_dir

    @staticmethod
    def _compute_conviction(
        basis_zscore: float,
        oi_div: float | None,
        basis_trigger: bool,
        div_trigger: bool,
    ) -> float:
        """Compute conviction from basis z-score and divergence strength.

        Basis component (0-0.6): scales with z-score beyond threshold.
        Divergence component (0-0.4): scales with divergence magnitude.
        """
        basis_score = 0.0
        if basis_trigger:
            basis_score = min((abs(basis_zscore) - 2.0) / 4.0 + 0.2, 0.6)
            basis_score = max(basis_score, 0.0)

        div_score = 0.0
        if div_trigger and oi_div is not None:
            div_score = min(abs(oi_div) / 10.0, 0.4)

        return round(min(basis_score + div_score, 1.0), 3)
