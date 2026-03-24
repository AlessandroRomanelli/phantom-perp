"""Alpha combiner — aggregates signals into ranked trade ideas.

Maintains a rolling buffer of recent signals.  When a new signal arrives
it checks for alignment or conflict with buffered signals from other
strategies:
- Aligned signals boost combined conviction.
- Conflicting signals are resolved via regime-aware weighting (or cancel).

A per-direction cooldown prevents emitting duplicate ideas in rapid succession.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PositionSide
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.utils import generate_id, utc_now
from libs.portfolio.router import PortfolioRouter

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
        cooldown: Minimum interval between ideas in the same direction.
    """

    def __init__(
        self,
        router: PortfolioRouter,
        regime_detector: RegimeDetector,
        scorecard: StrategyScorecard,
        combination_window: timedelta = timedelta(seconds=60),
        cooldown: timedelta = timedelta(seconds=30),
    ) -> None:
        self._router = router
        self._regime = regime_detector
        self._scorecard = scorecard
        self._window = combination_window
        self._cooldown = cooldown
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

        # Skip if we recently emitted an idea in this direction for this instrument
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
        regime = self._regime.current_regime

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
            portfolio_target=target,
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
        """True if we recently emitted an idea in this direction for this instrument."""
        cutoff = now - self._cooldown
        return any(
            ts > cutoff and d == direction and inst == instrument
            for ts, d, inst in self._recent_ideas
        )

    @staticmethod
    def _best_price(
        signals: list[StandardSignal],
        attr: str,
    ) -> Decimal | None:
        """Return the price from the highest-conviction signal that has it."""
        for s in sorted(signals, key=lambda x: x.conviction, reverse=True):
            val = getattr(s, attr, None)
            if val is not None:
                return val
        return None

    @staticmethod
    def _combined_horizon(signals: list[StandardSignal]) -> timedelta:
        """Median time horizon from contributing signals."""
        horizons = sorted(s.time_horizon for s in signals)
        mid = len(horizons) // 2
        return horizons[mid]
