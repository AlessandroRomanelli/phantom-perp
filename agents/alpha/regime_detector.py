"""Market regime detection from streaming market data.

Classifies the market into one of six regimes (trending up/down, ranging,
high/low volatility, squeeze) using mark price history and realized
volatility from MarketSnapshot updates.
"""

from __future__ import annotations

from collections import deque

from libs.common.models.enums import MarketRegime
from libs.common.models.market_snapshot import MarketSnapshot


class RegimeDetector:
    """Classify the current market regime from streaming snapshots.

    Tracks a rolling window of mark prices and uses the snapshot's
    volatility fields to detect regime transitions.

    Args:
        lookback: Number of snapshots to retain for trend analysis.
        high_vol_threshold: Volatility above this → HIGH_VOLATILITY.
        low_vol_threshold: Volatility below this → LOW_VOLATILITY or SQUEEZE.
        trend_pct_threshold: Price deviation from mean (%) to declare a trend.
        squeeze_range_pct: Max price range (%) for squeeze detection.
    """

    def __init__(
        self,
        lookback: int = 50,
        high_vol_threshold: float = 0.60,
        low_vol_threshold: float = 0.20,
        trend_pct_threshold: float = 0.5,
        squeeze_range_pct: float = 0.3,
    ) -> None:
        self._prices: deque[float] = deque(maxlen=lookback)
        self._regime = MarketRegime.RANGING
        self._high_vol = high_vol_threshold
        self._low_vol = low_vol_threshold
        self._trend_pct = trend_pct_threshold
        self._squeeze_range = squeeze_range_pct

    @property
    def current_regime(self) -> MarketRegime:
        """The most recently detected regime."""
        return self._regime

    def update(self, snapshot: MarketSnapshot) -> MarketRegime:
        """Incorporate a new snapshot and return the updated regime.

        Args:
            snapshot: Latest market data point.

        Returns:
            The detected MarketRegime after this update.
        """
        self._prices.append(float(snapshot.last_price))
        vol = snapshot.volatility_24h

        if len(self._prices) < 10:
            return self._regime

        # High volatility overrides everything
        if vol > self._high_vol:
            self._regime = MarketRegime.HIGH_VOLATILITY
            return self._regime

        # Low volatility: check for squeeze (narrow range) or just quiet
        if vol < self._low_vol:
            prices = list(self._prices)
            mean = sum(prices) / len(prices)
            if mean > 0:
                price_range_pct = (max(prices) - min(prices)) / mean * 100
                if price_range_pct < self._squeeze_range:
                    self._regime = MarketRegime.SQUEEZE
                    return self._regime
            self._regime = MarketRegime.LOW_VOLATILITY
            return self._regime

        # Normal vol: detect trend via price deviation from rolling mean
        prices = list(self._prices)
        mean = sum(prices) / len(prices)
        current = prices[-1]

        if mean > 0:
            deviation_pct = (current - mean) / mean * 100
            if deviation_pct > self._trend_pct:
                self._regime = MarketRegime.TRENDING_UP
            elif deviation_pct < -self._trend_pct:
                self._regime = MarketRegime.TRENDING_DOWN
            else:
                self._regime = MarketRegime.RANGING

        return self._regime
