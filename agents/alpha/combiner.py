"""Alpha combiner — aggregates signals into ranked trade ideas.

Maintains a rolling buffer of recent signals.  When a new signal arrives
it checks for alignment or conflict with buffered signals from other
strategies:
- Aligned signals boost combined conviction.
- Conflicting signals are resolved via regime-aware weighting (or cancel).

A bidirectional cooldown prevents emitting ideas in rapid succession,
and a separate flip interval prevents direction reversals that churn fees.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

from libs.common.models.enums import PositionSide
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.utils import generate_id, utc_now
from libs.portfolio.router import RouteRouter

from agents.alpha.conflict_resolver import resolve_conflicts
from agents.alpha.regime_detector import RegimeDetector
from agents.alpha.scorecard import StrategyScorecard


@dataclass(slots=True)
class _BufferedSignal:
    """Signal in the combination buffer with tracking metadata."""

    signal: StandardSignal
    received_at: datetime
    consumed: bool = False


class AlphaCombiner:
    """Aggregate signals, resolve conflicts, route to portfolios.

    Args:
        router: Portfolio router (A vs B).
        regime_detector: Current market regime source.
        scorecard: Rolling strategy accuracy tracker.
        combination_window: How long to keep signals for potential combination.
        cooldown: Minimum interval between ANY ideas for the same instrument.
        min_flip_interval: Minimum time before reversing direction on an instrument.
    """

    def __init__(
        self,
        router: RouteRouter,
        regime_detector: RegimeDetector,
        scorecard: StrategyScorecard,
        combination_window: timedelta = timedelta(seconds=60),
        cooldown: timedelta = timedelta(seconds=30),
        min_flip_interval: timedelta = timedelta(seconds=180),
    ) -> None:
        self._router = router
        self._regime = regime_detector
        self._scorecard = scorecard
        self._window = combination_window
        self._cooldown = cooldown
        self._min_flip_interval = min_flip_interval
        self._buffer: deque[_BufferedSignal] = deque(maxlen=200)
        self._recent_ideas: deque[tuple[datetime, PositionSide, str]] = deque(maxlen=50)

    def add_signal(
        self,
        signal: StandardSignal,
        now: datetime | None = None,
    ) -> list[RankedTradeIdea]:
        """Process a new signal and return any resulting trade ideas.

        Args:
            signal: Incoming signal from the signals agent.
            now: Override current time (for testing). Defaults to utc_now().

        Returns:
            List of 0 or 1 trade ideas (empty if filtered/cancelled).
        """
        now = now or utc_now()
        self._prune_buffer(now)
        self._buffer.append(_BufferedSignal(signal=signal, received_at=now))

        # Skip if we recently emitted any idea for this instrument (bidirectional)
        # or if this would be a direction flip within the min_flip_interval
        if self._in_cooldown(signal.direction, signal.instrument, now):
            return []

        # Gather unconsumed signals for this instrument only
        active = [
            b for b in self._buffer
            if not b.consumed and b.signal.instrument == signal.instrument
        ]
        if not active:
            return []

        active_signals = [b.signal for b in active]
        regime = self._regime.regime_for(signal.instrument)

        resolved = resolve_conflicts(
            active_signals, regime, self._scorecard,
        )

        if resolved is None:
            # Signals cancelled out — mark same-instrument signals as consumed
            for b in active:
                b.consumed = True
            return []

        # Collect contributing signals (same direction AND same instrument)
        contributing = [
            b.signal for b in active
            if b.signal.direction == resolved.direction
            and b.signal.instrument == signal.instrument
        ]

        # Use the best entry/SL/TP and median horizon from contributors
        entry_price = self._best_price(contributing, "entry_price")
        stop_loss = self._best_price(contributing, "stop_loss")
        take_profit = self._best_price(contributing, "take_profit")
        time_horizon = self._combined_horizon(contributing)

        # Route using the highest-conviction contributing signal
        best_signal = max(contributing, key=lambda s: s.conviction)
        target = self._router.route(best_signal)

        idea = RankedTradeIdea(
            idea_id=generate_id("idea"),
            timestamp=now,
            instrument=signal.instrument,
            route=target,
            direction=resolved.direction,
            conviction=resolved.conviction,
            sources=resolved.sources,
            time_horizon=time_horizon,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=resolved.reasoning,
            metadata={
                "regime": regime.value,
                "contributing_signals": len(contributing),
            },
        )

        # Mark contributing signals as consumed
        for b in active:
            if (b.signal.direction == resolved.direction
                    and b.signal.instrument == signal.instrument):
                b.consumed = True

        self._recent_ideas.append((now, resolved.direction, signal.instrument))
        return [idea]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune_buffer(self, now: datetime) -> None:
        """Remove signals older than the combination window."""
        cutoff = now - self._window
        while self._buffer and self._buffer[0].received_at < cutoff:
            self._buffer.popleft()

    def _in_cooldown(
        self, direction: PositionSide, instrument: str, now: datetime,
    ) -> bool:
        """True if a recent idea blocks this one.

        Two checks:
        1. Bidirectional cooldown — any idea for this instrument within
           ``self._cooldown`` blocks regardless of direction.
        2. Flip guard — an idea in the *opposite* direction within
           ``self._min_flip_interval`` blocks to prevent fee churn.
        """
        cooldown_cutoff = now - self._cooldown
        flip_cutoff = now - self._min_flip_interval
        for ts, d, inst in self._recent_ideas:
            if inst != instrument:
                continue
            # Bidirectional: any recent idea blocks
            if ts > cooldown_cutoff:
                return True
            # Flip guard: opposite direction within longer window blocks
            if ts > flip_cutoff and d != direction:
                return True
        return False

    @staticmethod
    def _best_price(
        signals: list[StandardSignal],
        attr: str,
    ) -> Decimal | None:
        """Return the price from the highest-conviction signal that has it."""
        for s in sorted(signals, key=lambda x: x.conviction, reverse=True):
            val = getattr(s, attr, None)
            if val is not None:
                return cast("Decimal | None", val)
        return None

    @staticmethod
    def _combined_horizon(signals: list[StandardSignal]) -> timedelta:
        """Median time horizon from contributing signals."""
        horizons = sorted(s.time_horizon for s in signals)
        mid = len(horizons) // 2
        if len(horizons) % 2 == 0 and len(horizons) > 1:
            return (horizons[mid - 1] + horizons[mid]) / 2
        return horizons[mid]
