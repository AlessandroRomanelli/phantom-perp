"""Order batching — groups orders arriving within a short window.

When multiple signals fire near-simultaneously, we group them into a single
Telegram message rather than spamming the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from libs.common.models.order import ProposedOrder


@dataclass
class OrderBatcher:
    """Collects orders and flushes them as batches."""

    window: timedelta = timedelta(seconds=30)
    max_batch_size: int = 5
    _buffer: list[ProposedOrder] = field(default_factory=list)
    _window_start: datetime | None = None

    def add(self, order: ProposedOrder, now: datetime) -> list[ProposedOrder] | None:
        """Add an order to the current batch.

        Returns a batch (list) when either:
        - The batch window has elapsed since the first order, or
        - max_batch_size is reached.

        Returns None if the order was buffered and the batch is not yet ready.
        """
        # If buffer is empty, start a new window
        if not self._buffer:
            self._window_start = now
            self._buffer.append(order)
            return None

        # Check if the window has elapsed since the first order
        assert self._window_start is not None
        if now >= self._window_start + self.window:
            # Flush existing batch, start a new window with this order
            batch = list(self._buffer)
            self._buffer.clear()
            self._window_start = now
            self._buffer.append(order)
            return batch

        self._buffer.append(order)

        # Check if we've hit max batch size
        if len(self._buffer) >= self.max_batch_size:
            batch = list(self._buffer)
            self._buffer.clear()
            self._window_start = None
            return batch

        return None

    def flush(self) -> list[ProposedOrder] | None:
        """Force-flush the current buffer regardless of timing.

        Returns the buffered orders, or None if empty.
        """
        if not self._buffer:
            return None
        batch = list(self._buffer)
        self._buffer.clear()
        self._window_start = None
        return batch

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        return len(self._buffer) == 0
