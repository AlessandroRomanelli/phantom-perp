"""Redis cache helpers for shared state and rate limiting."""

from __future__ import annotations

from typing import Any

import orjson
import redis.asyncio as aioredis


class RedisCache:
    """Async Redis cache for shared agent state.

    Used for caching latest market snapshots, portfolio states,
    and other frequently-accessed data.

    Args:
        redis_url: Redis connection URL.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=False,
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        """Get a cached JSON value.

        Args:
            key: Cache key.

        Returns:
            Parsed dict if the key exists, None otherwise.
        """
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return orjson.loads(raw)

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        """Set a JSON value in the cache.

        Args:
            key: Cache key.
            value: Dict to serialize and store.
            ttl_seconds: Optional TTL in seconds.
        """
        payload = orjson.dumps(value)
        if ttl_seconds:
            await self._redis.setex(key, ttl_seconds, payload)
        else:
            await self._redis.set(key, payload)

    async def delete(self, key: str) -> None:
        """Delete a cache key."""
        await self._redis.delete(key)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()
