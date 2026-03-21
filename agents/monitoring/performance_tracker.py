"""Per-portfolio performance tracking: returns, drawdown, Sharpe, win rate."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(slots=True)
class EquitySample:
    """A timestamped equity observation."""

    timestamp: datetime
    equity_usdc: Decimal


@dataclass(slots=True)
class PerformanceSummary:
    """Aggregated performance metrics for a portfolio."""

    total_return_pct: float
    peak_equity_usdc: Decimal
    trough_equity_usdc: Decimal
    max_drawdown_pct: float
    current_drawdown_pct: float
    sharpe_ratio: float | None
    win_count: int
    loss_count: int
    win_rate: float
    sample_count: int


@dataclass(slots=True)
class PerformanceTracker:
    """Tracks equity curve and computes performance metrics for one portfolio.

    Call record_equity() with each PortfolioSnapshot to accumulate the
    equity curve.  Call summary() at any time for current metrics.
    """

    starting_equity_usdc: Decimal
    _samples: list[EquitySample] = field(default_factory=list)
    _trade_results: list[Decimal] = field(default_factory=list)
    _peak_equity: Decimal = Decimal("0")
    _trough_equity: Decimal | None = None
    _max_drawdown_pct: float = 0.0

    def record_equity(self, equity_usdc: Decimal, timestamp: datetime) -> None:
        """Record an equity observation and update running stats."""
        self._samples.append(EquitySample(timestamp=timestamp, equity_usdc=equity_usdc))
        if equity_usdc > self._peak_equity:
            self._peak_equity = equity_usdc
        if self._trough_equity is None or equity_usdc < self._trough_equity:
            self._trough_equity = equity_usdc
        if self._peak_equity > 0:
            dd = float((self._peak_equity - equity_usdc) / self._peak_equity) * 100
            if dd > self._max_drawdown_pct:
                self._max_drawdown_pct = dd

    def record_trade_result(self, pnl_usdc: Decimal) -> None:
        """Record whether a closed trade was a win or loss.

        This is tracked separately from equity samples so that win rate
        counts actual trades, not equity snapshots.
        """
        self._trade_results.append(pnl_usdc)

    def _current_drawdown_pct(self) -> float:
        if not self._samples or self._peak_equity <= 0:
            return 0.0
        current = self._samples[-1].equity_usdc
        return float((self._peak_equity - current) / self._peak_equity) * 100

    def _compute_returns(self, window: timedelta | None = None) -> list[float]:
        """Compute period-over-period returns for the equity curve."""
        samples = self._samples
        if window is not None and len(samples) > 1:
            cutoff = samples[-1].timestamp - window
            samples = [s for s in samples if s.timestamp >= cutoff]

        if len(samples) < 2:
            return []

        returns: list[float] = []
        for i in range(1, len(samples)):
            prev = samples[i - 1].equity_usdc
            if prev > 0:
                ret = float((samples[i].equity_usdc - prev) / prev)
                returns.append(ret)
        return returns

    def compute_sharpe(
        self,
        annualization_factor: float = math.sqrt(365 * 24),
        risk_free_rate: float = 0.0,
    ) -> float | None:
        """Compute annualized Sharpe ratio from hourly equity samples.

        Default annualization: sqrt(365*24) for hourly observations in
        a 24/7 market.  Returns None if fewer than 2 samples.
        """
        returns = self._compute_returns()
        if len(returns) < 2:
            return None

        mean_ret = sum(returns) / len(returns)
        excess = mean_ret - risk_free_rate
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return (excess / std) * annualization_factor

    def summary(self) -> PerformanceSummary:
        """Build a snapshot of all performance metrics."""
        wins = sum(1 for r in self._trade_results if r > 0)
        losses = sum(1 for r in self._trade_results if r <= 0)
        total_trades = wins + losses

        if self.starting_equity_usdc > 0 and self._samples:
            current = self._samples[-1].equity_usdc
            total_return = float(
                (current - self.starting_equity_usdc) / self.starting_equity_usdc,
            ) * 100
        else:
            total_return = 0.0

        return PerformanceSummary(
            total_return_pct=total_return,
            peak_equity_usdc=self._peak_equity,
            trough_equity_usdc=self._trough_equity or Decimal("0"),
            max_drawdown_pct=self._max_drawdown_pct,
            current_drawdown_pct=self._current_drawdown_pct(),
            sharpe_ratio=self.compute_sharpe(),
            win_count=wins,
            loss_count=losses,
            win_rate=(wins / total_trades * 100) if total_trades > 0 else 0.0,
            sample_count=len(self._samples),
        )


@dataclass(slots=True)
class DualPerformanceTracker:
    """Manages independent performance trackers for both portfolios."""

    tracker_a: PerformanceTracker
    tracker_b: PerformanceTracker

    @property
    def combined_return_pct(self) -> float:
        """Weighted combined return based on starting equity."""
        total_start = self.tracker_a.starting_equity_usdc + self.tracker_b.starting_equity_usdc
        if total_start <= 0:
            return 0.0
        a_current = (
            self.tracker_a._samples[-1].equity_usdc
            if self.tracker_a._samples
            else self.tracker_a.starting_equity_usdc
        )
        b_current = (
            self.tracker_b._samples[-1].equity_usdc
            if self.tracker_b._samples
            else self.tracker_b.starting_equity_usdc
        )
        return float((a_current + b_current - total_start) / total_start) * 100
