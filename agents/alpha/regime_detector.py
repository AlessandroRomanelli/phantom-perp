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

    Maintains per-instrument price histories so that snapshots from
    different instruments do not corrupt each other's trend/squeeze
    detection.

    Args:
        lookback: Number of snapshots to retain per instrument for trend analysis.
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
        self._lookback = lookback
        self._prices: dict[str, deque[float]] = {}
        self._regimes: dict[str, MarketRegime] = {}
        self._last_instrument: str | None = None
        self._high_vol = high_vol_threshold
        self._low_vol = low_vol_threshold
        self._trend_pct = trend_pct_threshold
        self._squeeze_range = squeeze_range_pct

    @property
    def regimes(self) -> dict[str, MarketRegime]:
        """All per-instrument regime classifications (copy of internal state)."""
        return dict(self._regimes)

    @property
    def current_regime(self) -> MarketRegime:
        """The regime for the most recently updated instrument."""
        if self._last_instrument is not None:
            return self._regimes.get(self._last_instrument, MarketRegime.RANGING)
        return MarketRegime.RANGING

    def snapshot_count_for(self, instrument: str) -> int:
        """Number of price snapshots seen for a specific instrument."""
        return len(self._prices.get(instrument, []))

    def regime_for(self, instrument: str) -> MarketRegime:
        """The detected regime for a specific instrument."""
        return self._regimes.get(instrument, MarketRegime.RANGING)

    def update(self, snapshot: MarketSnapshot) -> MarketRegime:
        """Incorporate a new snapshot and return the updated regime.

        Args:
            snapshot: Latest market data point.

        Returns:
            The detected MarketRegime after this update.
        """
        instrument = snapshot.instrument
        self._last_instrument = instrument

        if instrument not in self._prices:
            self._prices[instrument] = deque(maxlen=self._lookback)

        self._prices[instrument].append(float(snapshot.last_price))
        vol = snapshot.volatility_24h
        prices_deque = self._prices[instrument]

        if len(prices_deque) < 10:
            return self._regimes.get(instrument, MarketRegime.RANGING)

        regime = self._classify(list(prices_deque), vol)
        self._regimes[instrument] = regime
        return regime

    def _classify(self, prices: list[float], vol: float) -> MarketRegime:
        """Classify regime from a single instrument's price history."""
        # High volatility overrides everything
        if vol > self._high_vol:
            return MarketRegime.HIGH_VOLATILITY

        # Low volatility: check for squeeze (narrow range) or just quiet
        if vol < self._low_vol:
            mean = sum(prices) / len(prices)
            if mean > 0:
                price_range_pct = (max(prices) - min(prices)) / mean * 100
                if price_range_pct < self._squeeze_range:
                    return MarketRegime.SQUEEZE
            return MarketRegime.LOW_VOLATILITY

        # Normal vol: detect trend via price deviation from rolling mean
        mean = sum(prices) / len(prices)
        current = prices[-1]

        if mean > 0:
            deviation_pct = (current - mean) / mean * 100
            if deviation_pct > self._trend_pct:
                return MarketRegime.TRENDING_UP
            elif deviation_pct < -self._trend_pct:
                return MarketRegime.TRENDING_DOWN

        return MarketRegime.RANGING
