"""Tests for the rolling strategy scorecard."""

from libs.common.models.enums import SignalSource

from agents.alpha.scorecard import StrategyScorecard


class TestStrategyScorecard:
    def test_default_weight_for_unknown_strategy(self) -> None:
        sc = StrategyScorecard()
        assert sc.weight(SignalSource.MOMENTUM) == 1.0

    def test_accuracy_with_no_data_returns_none(self) -> None:
        sc = StrategyScorecard()
        assert sc.accuracy(SignalSource.MOMENTUM) is None

    def test_accuracy_tracks_correctly(self) -> None:
        sc = StrategyScorecard()
        sc.record_outcome(SignalSource.MOMENTUM, True)
        sc.record_outcome(SignalSource.MOMENTUM, True)
        sc.record_outcome(SignalSource.MOMENTUM, False)
        assert sc.accuracy(SignalSource.MOMENTUM) == 2 / 3

    def test_weight_stays_default_below_min_samples(self) -> None:
        """Weight doesn't adjust until at least 10 samples."""
        sc = StrategyScorecard()
        for _ in range(9):
            sc.record_outcome(SignalSource.MOMENTUM, True)
        # Still at default — not enough samples
        assert sc.weight(SignalSource.MOMENTUM) == 1.0

    def test_weight_adjusts_after_enough_samples(self) -> None:
        sc = StrategyScorecard()
        # 10 observations: 7 correct
        for _ in range(7):
            sc.record_outcome(SignalSource.MOMENTUM, True)
        for _ in range(3):
            sc.record_outcome(SignalSource.MOMENTUM, False)
        assert sc.weight(SignalSource.MOMENTUM) == 0.7

    def test_weight_floors_at_minimum(self) -> None:
        sc = StrategyScorecard()
        # 10 observations: all incorrect → accuracy = 0, but floored at 0.2
        for _ in range(10):
            sc.record_outcome(SignalSource.FUNDING_ARB, False)
        assert sc.weight(SignalSource.FUNDING_ARB) == 0.2

    def test_custom_weight_override(self) -> None:
        sc = StrategyScorecard()
        sc.set_weight(SignalSource.MOMENTUM, 0.5)
        assert sc.weight(SignalSource.MOMENTUM) == 0.5

    def test_custom_weight_overrides_accuracy(self) -> None:
        """Custom weight takes precedence over computed accuracy."""
        sc = StrategyScorecard()
        for _ in range(15):
            sc.record_outcome(SignalSource.MOMENTUM, True)
        sc.set_weight(SignalSource.MOMENTUM, 0.3)
        assert sc.weight(SignalSource.MOMENTUM) == 0.3

    def test_window_limits_records(self) -> None:
        sc = StrategyScorecard(window=5)
        # Fill window with correct
        for _ in range(5):
            sc.record_outcome(SignalSource.MOMENTUM, True)
        assert sc.accuracy(SignalSource.MOMENTUM) == 1.0
        # Add incorrect — oldest correct is evicted
        sc.record_outcome(SignalSource.MOMENTUM, False)
        assert sc.accuracy(SignalSource.MOMENTUM) == 4 / 5

    def test_sample_counts(self) -> None:
        sc = StrategyScorecard()
        sc.record_outcome(SignalSource.MOMENTUM, True)
        sc.record_outcome(SignalSource.MOMENTUM, False)
        sc.record_outcome(SignalSource.FUNDING_ARB, True)
        counts = sc.sample_counts
        assert counts[SignalSource.MOMENTUM] == 2
        assert counts[SignalSource.FUNDING_ARB] == 1

    def test_independent_tracking_per_source(self) -> None:
        sc = StrategyScorecard()
        for _ in range(10):
            sc.record_outcome(SignalSource.MOMENTUM, True)
        for _ in range(10):
            sc.record_outcome(SignalSource.FUNDING_ARB, False)
        assert sc.weight(SignalSource.MOMENTUM) == 1.0
        assert sc.weight(SignalSource.FUNDING_ARB) == 0.2
