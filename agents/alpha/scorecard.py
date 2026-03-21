"""Rolling strategy accuracy tracker for alpha weighting.

Tracks outcomes per signal source and derives weights.  New strategies
start with a default weight of 1.0; after enough observations the weight
adjusts to reflect rolling accuracy (floored at 0.2 to avoid zeroing out
a strategy entirely).
"""

from __future__ import annotations

from collections import defaultdict, deque

from libs.common.models.enums import SignalSource

_DEFAULT_WEIGHT = 1.0
_MIN_WEIGHT = 0.2
_MIN_SAMPLES_FOR_ADJUSTMENT = 10


class StrategyScorecard:
    """Per-strategy rolling accuracy tracker.

    Args:
        window: Maximum number of outcomes to track per strategy.
    """

    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._records: dict[SignalSource, deque[bool]] = defaultdict(
            lambda: deque(maxlen=window),
        )
        self._custom_weights: dict[SignalSource, float] = {}

    def record_outcome(self, source: SignalSource, was_correct: bool) -> None:
        """Record whether a signal from this source was correct."""
        self._records[source].append(was_correct)

    def accuracy(self, source: SignalSource) -> float | None:
        """Rolling accuracy for a strategy, or None if no data."""
        records = self._records.get(source)
        if not records or len(records) == 0:
            return None
        return sum(records) / len(records)

    def weight(self, source: SignalSource) -> float:
        """Effective weight for a strategy.

        Returns a custom override if set, otherwise derives from accuracy
        (default 1.0 until enough samples are collected).
        """
        if source in self._custom_weights:
            return self._custom_weights[source]

        records = self._records.get(source)
        if not records or len(records) < _MIN_SAMPLES_FOR_ADJUSTMENT:
            return _DEFAULT_WEIGHT

        acc = sum(records) / len(records)
        return max(_MIN_WEIGHT, acc)

    def set_weight(self, source: SignalSource, weight: float) -> None:
        """Override the weight for a strategy."""
        self._custom_weights[source] = weight

    @property
    def sample_counts(self) -> dict[SignalSource, int]:
        """Number of recorded outcomes per strategy."""
        return {s: len(r) for s, r in self._records.items()}
