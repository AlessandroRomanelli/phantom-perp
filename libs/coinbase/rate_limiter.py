"""Token-bucket rate limiter for Coinbase INTX API.

Each API key has its own rate limit budget. Since Coinbase INTX API keys
are portfolio-scoped, each portfolio gets its own RateLimiter instance
via the CoinbaseClientPool.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Async token-bucket rate limiter for Coinbase INTX API.

    Tokens replenish at a fixed rate. Each API call consumes one token.
    When the bucket is empty, callers are awaited until a token is available.
    Respects rate-limit headers from Coinbase responses.

    Args:
        max_tokens: Maximum burst capacity.
        refill_rate: Tokens added per second.
        buffer_pct: Reserve buffer percentage (don't use the last N% of capacity).
    """

    def __init__(
        self,
        max_tokens: int = 30,
        refill_rate: float = 10.0,
        buffer_pct: float = 20.0,
    ) -> None:
        self._max_tokens = max_tokens
        self._refill_rate = refill_rate
        self._buffer_pct = buffer_pct
        self._effective_max = max_tokens * (1.0 - buffer_pct / 100.0)
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._effective_max,
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to consume (usually 1 per API call).
        """
        async with self._lock:
            self._refill()
            while self._tokens < tokens:
                deficit = tokens - self._tokens
                wait_time = deficit / self._refill_rate
                await asyncio.sleep(wait_time)
                self._refill()
            self._tokens -= tokens

    def update_from_headers(self, remaining: int | None, reset_at: float | None) -> None:
        """Update internal state from Coinbase rate-limit response headers.

        Call this after every API response with the values of:
            RateLimit-Remaining: number of requests left in the window
            RateLimit-Reset: unix timestamp when the window resets

        Args:
            remaining: Value of RateLimit-Remaining header, if present.
            reset_at: Value of RateLimit-Reset header as unix timestamp, if present.
        """
        if remaining is not None:
            effective_remaining = remaining * (1.0 - self._buffer_pct / 100.0)
            self._tokens = min(self._effective_max, effective_remaining)

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens (approximate, not thread-safe)."""
        self._refill()
        return self._tokens
