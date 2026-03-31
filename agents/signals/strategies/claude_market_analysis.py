"""Claude Market Analysis strategy — async queue-based signal drain.

This strategy does not compute signals itself.  Instead it acts as a
*bridge*: the Claude scheduler (T02) asynchronously analyses market state,
constructs ``StandardSignal`` objects, and pushes them onto a dedicated
``asyncio.Queue``.  On every ``evaluate()`` call the strategy drains that
queue and returns whatever signals have accumulated since the last tick.

Design rationale:
- The ``SignalStrategy`` ABC is synchronous; Claude inference is inherently
  async and slow.  Decoupling via a queue lets inference run on its own
  cadence without blocking the main signals loop.
- ``evaluate()`` is non-blocking: ``queue.get_nowait()`` + ``asyncio.QueueEmpty``
  guarantees O(N) drain with zero await.
- If no queue has been wired (e.g. unit tests, dry-run), ``evaluate()``
  returns ``[]`` safely.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy
from libs.common.logging import setup_logging
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal

_log = setup_logging("claude_market_analysis", json_output=False)


@dataclass(frozen=True)
class ClaudeMarketAnalysisParams:
    """Tunable parameters for the Claude Market Analysis strategy.

    Attributes:
        enabled: Whether the strategy is active.
        weight: Alpha combiner weight (0.0–1.0).
        analysis_interval_seconds: How often Claude re-analyses each
            instrument (scheduler cadence).
        max_queue_size: Upper bound for the per-strategy signal queue;
            prevents unbounded growth if the signals loop falls behind.
        min_conviction: Minimum conviction threshold.  Signals below this
            level are dropped by the scheduler before enqueueing.
        route_a_min_conviction: Conviction floor for Route A routing.
        default_time_horizon_hours: Default signal horizon when the LLM does
            not specify one explicitly.
    """

    enabled: bool = True
    weight: float = 0.15
    analysis_interval_seconds: int = 300  # 5 minutes
    max_queue_size: int = 50
    min_conviction: float = 0.50
    route_a_min_conviction: float = 0.75
    default_time_horizon_hours: float = 4.0


class ClaudeMarketAnalysisStrategy(SignalStrategy):
    """Queue-draining bridge strategy that surfaces Claude-generated signals.

    The strategy shell itself contains no inference logic.  A sibling
    scheduler component (wired at agent startup) pushes ``StandardSignal``
    objects into the queue returned by :py:meth:`get_queue`.  On each
    ``evaluate()`` call the strategy drains the queue and returns all
    pending signals.

    Args:
        params: Strategy parameters.  Uses defaults if ``None``.
        config: Optional YAML config dict; overrides defaults when present.
    """

    def __init__(
        self,
        params: ClaudeMarketAnalysisParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or ClaudeMarketAnalysisParams()

        if config:
            p = config.get("parameters", {})
            self._params = ClaudeMarketAnalysisParams(
                enabled=p.get("enabled", self._params.enabled),
                weight=p.get("weight", self._params.weight),
                analysis_interval_seconds=p.get(
                    "analysis_interval_seconds",
                    self._params.analysis_interval_seconds,
                ),
                max_queue_size=p.get("max_queue_size", self._params.max_queue_size),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                route_a_min_conviction=p.get(
                    "route_a_min_conviction",
                    self._params.route_a_min_conviction,
                ),
                default_time_horizon_hours=p.get(
                    "default_time_horizon_hours",
                    self._params.default_time_horizon_hours,
                ),
            )

        self._enabled: bool = self._params.enabled
        # Queue is None until wired by the scheduler (T02 / T03)
        self._queue: asyncio.Queue[StandardSignal] | None = None

    # ------------------------------------------------------------------
    # SignalStrategy interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "claude_market_analysis"

    @property
    def enabled(self) -> bool:
        """Whether this strategy is currently active."""
        return self._enabled

    @property
    def min_history(self) -> int:
        """No warm-up required — signals arrive externally via the queue."""
        return 1

    @property
    def default_time_horizon(self) -> timedelta:
        """Default time horizon used when the scheduler omits an explicit one."""
        return timedelta(hours=self._params.default_time_horizon_hours)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Drain the signal queue and return all accumulated signals.

        This method is intentionally non-blocking.  It calls
        ``queue.get_nowait()`` in a tight loop and stops as soon as
        ``asyncio.QueueEmpty`` is raised.  No inference happens here.

        Args:
            snapshot: Current market state (used only for logging context).
            store: Shared feature store (not consumed by this strategy).

        Returns:
            All signals queued since the previous ``evaluate()`` call,
            or an empty list if no queue has been wired yet.
        """
        if self._queue is None:
            return []

        signals: list[StandardSignal] = []
        while True:
            try:
                sig = self._queue.get_nowait()
                signals.append(sig)
            except asyncio.QueueEmpty:
                break

        if signals:
            _log.info(
                "claude_signals_drained",
                instrument=snapshot.instrument,
                count=len(signals),
                convictions=[round(s.conviction, 3) for s in signals],
            )

        return signals

    # ------------------------------------------------------------------
    # Queue wiring (called by scheduler / integration layer)
    # ------------------------------------------------------------------

    def set_queue(self, queue: asyncio.Queue[StandardSignal]) -> None:
        """Wire the external signal queue.

        Must be called before the first ``evaluate()`` tick if signals are
        expected.  Idempotent — calling again replaces the queue.

        Args:
            queue: The queue that the Claude scheduler will push signals into.
        """
        self._queue = queue
        _log.info(
            "claude_strategy_queue_wired",
            max_size=queue.maxsize,
        )

    def get_queue(self) -> asyncio.Queue[StandardSignal] | None:
        """Return the currently wired queue, or ``None`` if not yet wired."""
        return self._queue

    # ------------------------------------------------------------------
    # Accessors for downstream components
    # ------------------------------------------------------------------

    @property
    def params(self) -> ClaudeMarketAnalysisParams:
        """Expose resolved params so the scheduler can read cadence settings."""
        return self._params
