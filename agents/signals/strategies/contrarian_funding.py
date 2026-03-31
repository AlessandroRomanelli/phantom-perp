"""Contrarian funding rate strategy — trade against extreme crowded positioning.

Signal logic:
  1. Compute z-score of current funding rate vs lookback window.
  2. Extremely positive funding (longs crowded) → SHORT against the crowd.
  3. Extremely negative funding (shorts crowded) → LONG against the crowd.
  4. OI z-score confirmation: rising open interest amplifies the crowding signal.
  5. Persistence ratio: fraction of recent funding samples on the same side,
     confirming a persistent directional bias that is ripe for mean-reversion.
  6. Three-component conviction: funding z-score extremity (0–0.40)
     + OI confirmation (0–0.35) + funding persistence (0–0.25).
  7. ATR-based stops (tighter SL 1.5×, wider TP 3.0× to capture mean-reversion).
  8. Portfolio A routing for high-conviction signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np

from agents.signals.strategies.base import SignalStrategy
from libs.common.instruments import get_instrument
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

if TYPE_CHECKING:
    from agents.signals.feature_store import FeatureStore
    from libs.common.models.market_snapshot import MarketSnapshot


@dataclass
class ContrarianFundingParams:
    """Tunable parameters for the contrarian funding rate strategy."""

    zscore_threshold: float = 2.0
    lookback_hours: int = 168  # ~7 days of funding rate history
    min_funding_samples: int = 10
    oi_zscore_threshold: float = 1.0
    oi_lookback: int = 100
    persistence_lookback: int = 10
    persistence_min_ratio: float = 0.6
    min_conviction: float = 0.55
    max_holding_hours: int = 16
    atr_period: int = 14
    stop_loss_atr_mult: float = 1.5   # Tighter SL — contrarian trades cut losses fast
    take_profit_atr_mult: float = 3.0  # Wider TP — target is mean-reversion, not funding
    cooldown_bars: int = 12
    portfolio_a_min_conviction: float = 0.70
    enabled: bool = True


class ContrarianFundingStrategy(SignalStrategy):
    """Contrarian strategy that fades extreme funding rate crowding.

    When the perpetual funding rate is abnormally extreme (measured by
    z-score against a rolling window), rising open interest confirms that
    the crowd is over-positioned, and funding has been persistently extreme
    on one side, the strategy signals the *opposite* side expecting
    mean-reversion of the crowded positioning.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: ContrarianFundingParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or ContrarianFundingParams()

        if config:
            p = config.get("parameters", {})
            self._params = ContrarianFundingParams(
                zscore_threshold=p.get(
                    "zscore_threshold", self._params.zscore_threshold,
                ),
                lookback_hours=p.get(
                    "lookback_hours", self._params.lookback_hours,
                ),
                min_funding_samples=p.get(
                    "min_funding_samples", self._params.min_funding_samples,
                ),
                oi_zscore_threshold=p.get(
                    "oi_zscore_threshold", self._params.oi_zscore_threshold,
                ),
                oi_lookback=p.get("oi_lookback", self._params.oi_lookback),
                persistence_lookback=p.get(
                    "persistence_lookback", self._params.persistence_lookback,
                ),
                persistence_min_ratio=p.get(
                    "persistence_min_ratio", self._params.persistence_min_ratio,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                max_holding_hours=p.get(
                    "max_holding_hours", self._params.max_holding_hours,
                ),
                atr_period=p.get("atr_period", self._params.atr_period),
                stop_loss_atr_mult=p.get(
                    "stop_loss_atr_mult", self._params.stop_loss_atr_mult,
                ),
                take_profit_atr_mult=p.get(
                    "take_profit_atr_mult", self._params.take_profit_atr_mult,
                ),
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
        # Must match the YAML filename and strategy_matrix key for compatibility.
        return "funding_arb"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return self._params.atr_period + 5

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate funding rate crowding and emit contrarian signals."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params

        # Guard: need enough funding rate history for z-score
        funding_rates = store.funding_rates
        if len(funding_rates) < p.min_funding_samples:
            return []

        # Compute z-score of current funding rate
        cur_rate = float(funding_rates[-1])
        window = funding_rates[-min(len(funding_rates), p.lookback_hours):]
        mean = float(np.mean(window))
        std = float(np.std(window, ddof=1))

        if std < 1e-12:
            return []  # No variance in funding — no crowding signal

        z_score = (cur_rate - mean) / std

        # Z-score threshold gate
        if abs(z_score) < p.zscore_threshold:
            return []

        # OI z-score: rising OI confirms the crowd is building
        open_interests = store.open_interests
        oi_window = open_interests[-min(len(open_interests), p.oi_lookback):]
        if len(oi_window) >= 2:
            oi_mean = float(np.mean(oi_window))
            oi_std = float(np.std(oi_window, ddof=1))
            if oi_std < 1e-12:
                oi_z_score = 0.0
            else:
                cur_oi = float(oi_window[-1])
                oi_z_score = max((cur_oi - oi_mean) / oi_std, 0.0)
        else:
            oi_z_score = 0.0

        # Persistence ratio: fraction of recent funding samples with same sign as
        # current extreme. High persistence = crowd has been one-sided for a while.
        persist_window = funding_rates[-min(len(funding_rates), p.persistence_lookback):]
        if len(persist_window) > 0:
            if cur_rate > 0:
                same_side = float(np.sum(persist_window > 0))
            else:
                same_side = float(np.sum(persist_window < 0))
            persistence_ratio = same_side / len(persist_window)
        else:
            persistence_ratio = 0.0

        # Compute conviction from three components
        conviction = self._compute_conviction(z_score, oi_z_score, persistence_ratio)

        if conviction < p.min_conviction:
            return []

        # Direction: fade the crowded side (contrarian to funding flow)
        # Positive funding → longs are crowded → SHORT against them
        # Negative funding → shorts are crowded → LONG against them
        direction = PositionSide.SHORT if cur_rate > 0 else PositionSide.LONG

        # ATR for stops
        tick_size = get_instrument(snapshot.instrument).tick_size
        closes = store.closes
        highs = store.highs
        lows = store.lows

        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

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

        # Portfolio A routing for high-conviction signals
        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        reasoning = (
            f"Contrarian funding {'long' if direction == PositionSide.LONG else 'short'}: "
            f"rate={cur_rate:.6f}, z={z_score:+.2f}, "
            f"oi_z={oi_z_score:.2f}, "
            f"persistence={persistence_ratio:.2f}"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.CONTRARIAN_FUNDING,
            time_horizon=timedelta(hours=p.max_holding_hours),
            reasoning=reasoning,
            suggested_target=suggested_target,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "funding_rate": round(cur_rate, 8),
                "z_score": round(z_score, 4),
                "oi_z_score": round(oi_z_score, 4),
                "persistence_ratio": round(persistence_ratio, 4),
                "funding_mean": round(mean, 8),
                "funding_std": round(std, 8),
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_conviction(
        z_score: float,
        oi_z_score: float,
        persistence_ratio: float,
    ) -> float:
        """Compute conviction from three complementary components.

        Three-component model (max total = 1.0):
          - Funding extremity (0–0.40): scales from 0 at z=2 to 0.40 at z=5+.
          - OI confirmation (0–0.35): scales from 0 at oi_z=1 to 0.35 at oi_z=2+.
          - Persistence (0–0.25): scales from 0 at ratio=0.5 to 0.25 at ratio=1.0.

        Args:
            z_score: Funding rate z-score (signed; magnitude used for extremity).
            oi_z_score: Open interest z-score (non-negative; rising OI = confirmation).
            persistence_ratio: Fraction of recent funding samples on same side [0, 1].

        Returns:
            Conviction score in [0.0, 1.0], rounded to 3 decimal places.
        """
        # Funding extremity component: higher z = more extreme dislocation
        z_excess = abs(z_score) - 2.0  # Baseline at z=2
        z_comp = min(max(z_excess / 3.0 * 0.40, 0.0), 0.40)

        # OI confirmation component: rising OI amplifies the crowding signal
        oi_excess = oi_z_score - 1.0  # Baseline at oi_z=1
        oi_comp = min(max(oi_excess / 1.0 * 0.35, 0.0), 0.35)

        # Persistence component: sustained bias confirms mean-reversion opportunity
        persist_excess = persistence_ratio - 0.5  # Baseline at 50% same-side
        persist_comp = min(max(persist_excess / 0.5 * 0.25, 0.0), 0.25)

        return round(min(z_comp + oi_comp + persist_comp, 1.0), 3)
