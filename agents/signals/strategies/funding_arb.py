"""Funding arbitrage strategy — exploit extreme funding rate dislocations.

Signal logic:
  1. Compute z-score of current funding rate vs lookback window.
  2. Extreme positive funding (longs pay shorts) → SHORT to collect payment.
  3. Extreme negative funding (shorts pay longs) → LONG to collect payment.
  4. Annualized rate filter: skip if absolute annualized rate too low to justify risk.
  5. Settlement proximity boost: conviction increases as next settlement approaches
     (funding is realized at settlement — closer = more certain capture).
  6. 3-component conviction: z-score magnitude + annualized rate attractiveness
     + settlement proximity.
  7. ATR-based stops.
  8. Portfolio A routing for high-conviction signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy
from libs.common.instruments import get_instrument
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

# Coinbase perps settle funding 3 times per day (every 8 hours).
_SETTLEMENTS_PER_DAY = 3
_DAYS_PER_YEAR = 365


@dataclass
class FundingArbParams:
    """Tunable parameters for the funding arbitrage strategy."""

    zscore_threshold: float = 2.0
    min_annualized_rate_pct: float = 10.0
    lookback_hours: int = 168  # ~7 days of funding rate history
    min_conviction: float = 0.6
    max_holding_hours: int = 4
    settle_before_close_minutes: int = 10
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.5  # Wider stops — funding arb tolerates some drift
    take_profit_atr_mult: float = 1.5  # Tighter TP — target is funding capture, not trend
    cooldown_bars: int = 8
    portfolio_a_min_conviction: float = 0.70
    min_funding_samples: int = 10
    enabled: bool = True


class FundingArbStrategy(SignalStrategy):
    """Funding rate dislocation strategy for capturing extreme funding payments.

    When the perpetual funding rate is abnormally high or low (measured by
    z-score against a rolling window), the strategy signals the opposite
    side to collect the funding payment at the next settlement.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: FundingArbParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or FundingArbParams()

        if config:
            p = config.get("parameters", {})
            self._params = FundingArbParams(
                zscore_threshold=p.get(
                    "zscore_threshold", self._params.zscore_threshold,
                ),
                min_annualized_rate_pct=p.get(
                    "min_annualized_rate_pct", self._params.min_annualized_rate_pct,
                ),
                lookback_hours=p.get(
                    "lookback_hours", self._params.lookback_hours,
                ),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                max_holding_hours=p.get(
                    "max_holding_hours", self._params.max_holding_hours,
                ),
                settle_before_close_minutes=p.get(
                    "settle_before_close_minutes",
                    self._params.settle_before_close_minutes,
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
                min_funding_samples=p.get(
                    "min_funding_samples", self._params.min_funding_samples,
                ),
                enabled=p.get("enabled", self._params.enabled),
            )

        self._enabled = self._params.enabled
        self._bars_since_signal = self._params.cooldown_bars  # Start ready

    @property
    def name(self) -> str:
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
        """Evaluate funding rate dislocation and emit arb signals."""
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
            return []  # No variance in funding — nothing to arb

        z_score = (cur_rate - mean) / std

        # Z-score threshold gate
        if abs(z_score) < p.zscore_threshold:
            return []

        # Annualized rate filter: abs(rate) * 3 settlements/day * 365 days * 100
        annualized_pct = abs(cur_rate) * _SETTLEMENTS_PER_DAY * _DAYS_PER_YEAR * 100
        if annualized_pct < p.min_annualized_rate_pct:
            return []

        # Direction: oppose the funding flow
        # Positive funding → longs pay shorts → SHORT to collect
        # Negative funding → shorts pay longs → LONG to collect
        if cur_rate > 0:
            direction = PositionSide.SHORT
        else:
            direction = PositionSide.LONG

        # Compute conviction (3-component model)
        conviction = self._compute_conviction(
            z_score=z_score,
            annualized_pct=annualized_pct,
            hours_since_last_funding=snapshot.hours_since_last_funding,
        )

        if conviction < p.min_conviction:
            return []

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

        # Portfolio A routing
        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        reasoning = (
            f"Funding arb {'long' if direction == PositionSide.LONG else 'short'}: "
            f"rate={cur_rate:.6f}, z={z_score:+.2f}, "
            f"annualized={annualized_pct:.1f}%, "
            f"settle_in={snapshot.hours_since_last_funding:.2f}h"
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.FUNDING_ARB,
            time_horizon=timedelta(hours=p.max_holding_hours),
            reasoning=reasoning,
            suggested_target=suggested_target,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "funding_rate": round(cur_rate, 8),
                "z_score": round(z_score, 4),
                "annualized_pct": round(annualized_pct, 2),
                "funding_mean": round(mean, 8),
                "funding_std": round(std, 8),
                "hours_since_funding": round(
                    snapshot.hours_since_last_funding, 3,
                ),
                "atr": round(cur_atr, 2),
            },
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_conviction(
        z_score: float,
        annualized_pct: float,
        hours_since_last_funding: float,
    ) -> float:
        """Compute conviction from z-score, annualized rate, and settlement proximity.

        Three-component model:
          - Z-score magnitude (0-0.40): scales from 0 at z=2 to 0.40 at z=5+.
          - Annualized rate attractiveness (0-0.35): scales from 0 at 10% to 0.35 at 50%+.
          - Settlement proximity (0-0.25): closer to settlement = higher conviction
            because funding capture is more certain. Maxes out in the last hour.
        """
        # Z-score component: higher z = more extreme dislocation
        z_excess = abs(z_score) - 2.0  # Baseline at z=2
        z_score_comp = min(max(z_excess / 3.0 * 0.40, 0.0), 0.40)

        # Annualized rate component: higher rate = more attractive
        rate_excess = annualized_pct - 10.0  # Baseline at 10%
        rate_comp = min(max(rate_excess / 40.0 * 0.35, 0.0), 0.35)

        # Settlement proximity component: hours_since_last_funding is [0, ~8]
        # Closer to 8 hours means next settlement is imminent
        # Normalize: 0h since last = just settled (low urgency) → 0.0
        #            ~8h since last = about to settle (high urgency) → 0.25
        # Coinbase settles every 8 hours
        settlement_period = 8.0
        progress = min(hours_since_last_funding / settlement_period, 1.0)
        proximity_comp = progress * 0.25

        return round(min(z_score_comp + rate_comp + proximity_comp, 1.0), 3)
