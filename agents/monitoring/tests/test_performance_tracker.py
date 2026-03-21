"""Tests for per-portfolio performance tracking."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agents.monitoring.performance_tracker import (
    DualPerformanceTracker,
    PerformanceTracker,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestRecordEquity:
    def test_initial_peak(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        assert tracker._peak_equity == Decimal("10000")

    def test_peak_updates(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("11000"), T0 + timedelta(hours=1))
        assert tracker._peak_equity == Decimal("11000")

    def test_trough_tracks_minimum(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("9000"), T0 + timedelta(hours=1))
        tracker.record_equity(Decimal("9500"), T0 + timedelta(hours=2))
        assert tracker._trough_equity == Decimal("9000")


class TestDrawdown:
    def test_no_drawdown_on_gain(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("11000"), T0 + timedelta(hours=1))
        assert tracker._max_drawdown_pct == 0.0

    def test_drawdown_computed(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("9000"), T0 + timedelta(hours=1))
        # 10% drawdown from peak of 10000
        assert tracker._max_drawdown_pct == 10.0

    def test_max_drawdown_persists(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("8000"), T0 + timedelta(hours=1))  # 20% dd
        tracker.record_equity(Decimal("9500"), T0 + timedelta(hours=2))  # recovers
        assert tracker._max_drawdown_pct == 20.0

    def test_current_drawdown(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_equity(Decimal("9500"), T0 + timedelta(hours=1))
        summary = tracker.summary()
        assert summary.current_drawdown_pct == 5.0


class TestSharpe:
    def test_not_enough_samples(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        assert tracker.compute_sharpe() is None

    def test_constant_equity_zero_sharpe(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        for i in range(10):
            tracker.record_equity(Decimal("10000"), T0 + timedelta(hours=i))
        assert tracker.compute_sharpe() == 0.0

    def test_positive_returns_positive_sharpe(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        # Steadily increasing equity
        for i in range(24):
            equity = Decimal("10000") + Decimal(str(i * 10))
            tracker.record_equity(equity, T0 + timedelta(hours=i))
        sharpe = tracker.compute_sharpe()
        assert sharpe is not None
        assert sharpe > 0

    def test_negative_returns_negative_sharpe(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        for i in range(24):
            equity = Decimal("10000") - Decimal(str(i * 10))
            tracker.record_equity(equity, T0 + timedelta(hours=i))
        sharpe = tracker.compute_sharpe()
        assert sharpe is not None
        assert sharpe < 0


class TestTradeResults:
    def test_win_rate(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        tracker.record_trade_result(Decimal("100"))
        tracker.record_trade_result(Decimal("50"))
        tracker.record_trade_result(Decimal("-30"))
        summary = tracker.summary()
        assert summary.win_count == 2
        assert summary.loss_count == 1
        # 2/3 = 66.67%
        assert abs(summary.win_rate - 66.67) < 0.1

    def test_no_trades(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("10000"), T0)
        summary = tracker.summary()
        assert summary.win_rate == 0.0
        assert summary.win_count == 0
        assert summary.loss_count == 0


class TestSummary:
    def test_total_return(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("11000"), T0)
        summary = tracker.summary()
        assert summary.total_return_pct == 10.0

    def test_negative_return(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        tracker.record_equity(Decimal("9000"), T0)
        summary = tracker.summary()
        assert summary.total_return_pct == -10.0

    def test_sample_count(self) -> None:
        tracker = PerformanceTracker(starting_equity_usdc=Decimal("10000"))
        for i in range(5):
            tracker.record_equity(Decimal("10000"), T0 + timedelta(hours=i))
        assert tracker.summary().sample_count == 5


class TestDualPerformanceTracker:
    def test_combined_return(self) -> None:
        tracker_a = PerformanceTracker(starting_equity_usdc=Decimal("5000"))
        tracker_b = PerformanceTracker(starting_equity_usdc=Decimal("15000"))
        tracker_a.record_equity(Decimal("5500"), T0)  # +10%
        tracker_b.record_equity(Decimal("15000"), T0)  # flat
        dual = DualPerformanceTracker(tracker_a=tracker_a, tracker_b=tracker_b)
        # Combined: (5500 + 15000 - 20000) / 20000 = 2.5%
        assert dual.combined_return_pct == 2.5

    def test_combined_no_samples(self) -> None:
        tracker_a = PerformanceTracker(starting_equity_usdc=Decimal("5000"))
        tracker_b = PerformanceTracker(starting_equity_usdc=Decimal("15000"))
        dual = DualPerformanceTracker(tracker_a=tracker_a, tracker_b=tracker_b)
        # Falls back to starting equity
        assert dual.combined_return_pct == 0.0
