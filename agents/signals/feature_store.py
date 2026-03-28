"""Rolling feature store fed by MarketSnapshots.

Accumulates price and funding history at a configurable sampling
interval. Strategies read from this store to compute indicators.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray

from libs.common.models.market_snapshot import MarketSnapshot


class FeatureStore:
    """Rolling buffer of market features for indicator computation.

    Accumulates close prices, highs, lows, and funding rates from
    MarketSnapshots. Samples at most once per `sample_interval` to
    avoid flooding the buffer with sub-second WS ticks.

    Args:
        max_samples: Maximum number of samples to retain.
        sample_interval: Minimum time between samples.
    """

    def __init__(
        self,
        max_samples: int = 500,
        sample_interval: timedelta = timedelta(seconds=60),
    ) -> None:
        self._max_samples = max_samples
        self._sample_interval = sample_interval
        self._last_sample_time: datetime | None = None

        # Price series (sampled at interval)
        self._closes: deque[float] = deque(maxlen=max_samples)
        self._highs: deque[float] = deque(maxlen=max_samples)
        self._lows: deque[float] = deque(maxlen=max_samples)
        self._timestamps: deque[datetime] = deque(maxlen=max_samples)

        # Track intra-interval high/low for candle-like behavior
        self._interval_high: float = 0.0
        self._interval_low: float = float("inf")

        # Additional series (sampled alongside price)
        self._index_prices: deque[float] = deque(maxlen=max_samples)
        self._volumes: deque[float] = deque(maxlen=max_samples)
        self._open_interests: deque[float] = deque(maxlen=max_samples)
        self._orderbook_imbalances: deque[float] = deque(maxlen=max_samples)

        # Funding rate history (one per snapshot that carries funding data)
        self._funding_rates: deque[float] = deque(maxlen=max_samples)

    def update(self, snapshot: MarketSnapshot) -> bool:
        """Ingest a new MarketSnapshot, sampling if the interval has elapsed.

        Args:
            snapshot: Incoming market snapshot.

        Returns:
            True if a new sample was added (interval elapsed), False if skipped.
        """
        price = float(snapshot.last_price)

        # Track intra-interval high/low
        self._interval_high = max(self._interval_high, price)
        self._interval_low = min(self._interval_low, price)

        # Always update funding rate if present
        if snapshot.funding_rate != Decimal("0"):
            if (
                not self._funding_rates
                or float(snapshot.funding_rate) != self._funding_rates[-1]
            ):
                self._funding_rates.append(float(snapshot.funding_rate))

        # Check sampling interval
        if self._last_sample_time is not None:
            elapsed = snapshot.timestamp - self._last_sample_time
            if elapsed < self._sample_interval:
                return False

        # Record a sample
        self._closes.append(price)
        self._highs.append(self._interval_high)
        self._lows.append(self._interval_low)
        self._timestamps.append(snapshot.timestamp)
        self._index_prices.append(float(snapshot.index_price))
        self._volumes.append(float(snapshot.volume_24h))
        self._open_interests.append(float(snapshot.open_interest))
        self._orderbook_imbalances.append(snapshot.orderbook_imbalance)
        self._last_sample_time = snapshot.timestamp

        # Reset interval tracking
        self._interval_high = price
        self._interval_low = price

        return True

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialize store state to a JSON-compatible dict for persistence.

        Returns:
            Dict suitable for ``orjson.dumps``.  ``float('inf')`` is
            encoded as ``None`` since orjson cannot serialize infinity.
        """
        return {
            "version": 1,
            "max_samples": self._max_samples,
            "sample_interval_seconds": self._sample_interval.total_seconds(),
            "last_sample_time": (
                self._last_sample_time.isoformat() if self._last_sample_time else None
            ),
            "interval_high": self._interval_high,
            "interval_low": (
                self._interval_low if self._interval_low != float("inf") else None
            ),
            "closes": list(self._closes),
            "highs": list(self._highs),
            "lows": list(self._lows),
            "timestamps": [t.isoformat() for t in self._timestamps],
            "index_prices": list(self._index_prices),
            "volumes": list(self._volumes),
            "open_interests": list(self._open_interests),
            "orderbook_imbalances": list(self._orderbook_imbalances),
            "funding_rates": list(self._funding_rates),
        }

    @classmethod
    def from_checkpoint(
        cls,
        data: dict[str, Any],
        *,
        max_samples: int = 500,
        sample_interval: timedelta = timedelta(seconds=60),
    ) -> FeatureStore:
        """Restore a FeatureStore from a persisted checkpoint dict.

        Args:
            data: Checkpoint dict as produced by ``to_checkpoint()``.
            max_samples: Maximum samples (should match original store).
            sample_interval: Sampling interval (must match checkpoint).

        Returns:
            Restored FeatureStore instance.

        Raises:
            ValueError: If checkpoint version or sample_interval mismatch.
        """
        if data.get("version") != 1:
            raise ValueError(f"Unsupported checkpoint version: {data.get('version')}")
        if data["sample_interval_seconds"] != sample_interval.total_seconds():
            raise ValueError(
                f"sample_interval mismatch: checkpoint={data['sample_interval_seconds']}s, "
                f"expected={sample_interval.total_seconds()}s"
            )

        store = cls(max_samples=max_samples, sample_interval=sample_interval)

        last_sample = data.get("last_sample_time")
        store._last_sample_time = (
            datetime.fromisoformat(last_sample) if last_sample else None
        )
        store._interval_high = data["interval_high"]
        store._interval_low = (
            data["interval_low"] if data["interval_low"] is not None else float("inf")
        )

        store._closes.extend(data["closes"])
        store._highs.extend(data["highs"])
        store._lows.extend(data["lows"])
        store._timestamps.extend(
            datetime.fromisoformat(t) for t in data["timestamps"]
        )
        store._index_prices.extend(data["index_prices"])
        store._volumes.extend(data["volumes"])
        store._open_interests.extend(data["open_interests"])
        store._orderbook_imbalances.extend(data["orderbook_imbalances"])
        store._funding_rates.extend(data["funding_rates"])

        return store

    @property
    def sample_count(self) -> int:
        """Number of price samples currently stored."""
        return len(self._closes)

    @property
    def closes(self) -> NDArray[np.float64]:
        """Close prices as a numpy array."""
        return np.array(self._closes, dtype=np.float64)

    @property
    def highs(self) -> NDArray[np.float64]:
        """High prices as a numpy array."""
        return np.array(self._highs, dtype=np.float64)

    @property
    def lows(self) -> NDArray[np.float64]:
        """Low prices as a numpy array."""
        return np.array(self._lows, dtype=np.float64)

    @property
    def funding_rates(self) -> NDArray[np.float64]:
        """Funding rate history as a numpy array."""
        return np.array(self._funding_rates, dtype=np.float64)

    @property
    def funding_rate_count(self) -> int:
        """Number of funding rate samples stored."""
        return len(self._funding_rates)

    @property
    def index_prices(self) -> NDArray[np.float64]:
        """Index (spot) prices as a numpy array."""
        return np.array(self._index_prices, dtype=np.float64)

    @property
    def volumes(self) -> NDArray[np.float64]:
        """24h rolling volumes as a numpy array."""
        return np.array(self._volumes, dtype=np.float64)

    @property
    def open_interests(self) -> NDArray[np.float64]:
        """Open interest values as a numpy array."""
        return np.array(self._open_interests, dtype=np.float64)

    @property
    def orderbook_imbalances(self) -> NDArray[np.float64]:
        """Orderbook imbalance values as a numpy array."""
        return np.array(self._orderbook_imbalances, dtype=np.float64)

    @property
    def timestamps(self) -> NDArray[np.float64]:
        """Sample timestamps as Unix epoch seconds."""
        return np.array(
            [t.timestamp() for t in self._timestamps], dtype=np.float64
        )

    @property
    def bar_volumes(self) -> NDArray[np.float64]:
        """Per-bar volume deltas from consecutive 24h volume samples.

        Length is (sample_count - 1). Values can be negative when
        high-volume periods roll off the 24h window.
        """
        if len(self._volumes) < 2:
            return np.array([], dtype=np.float64)
        return np.diff(np.array(self._volumes, dtype=np.float64))

    @property
    def latest_close(self) -> float | None:
        """Most recent close price, or None if empty."""
        return self._closes[-1] if self._closes else None

    @property
    def latest_timestamp(self) -> datetime | None:
        """Timestamp of the most recent sample."""
        return self._timestamps[-1] if self._timestamps else None
