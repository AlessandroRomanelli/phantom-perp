"""Abstract base class for all trading signal strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal

from agents.signals.feature_store import FeatureStore


class SignalStrategy(ABC):
    """Base interface for signal generation strategies.

    Each strategy consumes MarketSnapshots, maintains whatever internal
    state it needs via the shared FeatureStore, and produces zero or
    more StandardSignal objects per evaluation.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this strategy is currently active."""

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Evaluate the strategy against the current market state.

        Called on every incoming MarketSnapshot. Should return an empty
        list if no signal is warranted.

        Args:
            snapshot: Current market state.
            store: Shared feature store with historical data.

        Returns:
            Zero or more StandardSignal instances.
        """

    @property
    def min_history(self) -> int:
        """Minimum number of price samples required before evaluation.

        Strategies that need indicator warm-up should override this.
        Default is 1 (no warm-up needed).
        """
        return 1
