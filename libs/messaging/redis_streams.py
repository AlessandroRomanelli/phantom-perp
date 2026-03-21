"""Redis Streams implementation of the Publisher/Consumer interfaces."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import orjson
import redis.asyncio as aioredis

from libs.messaging.base import Consumer, Publisher


class RedisPublisher(Publisher):
    """Publish messages to Redis Streams.

    Messages are serialized to JSON via orjson and stored as a single
    'data' field in the stream entry. Stream entries are auto-ID'd by Redis.

    Args:
        redis_url: Redis connection URL.
        max_stream_length: Approximate max entries per stream (MAXLEN ~).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        max_stream_length: int = 100_000,
    ) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=False,
        )
        self._max_len = max_stream_length

    async def publish(self, channel: str, message: dict[str, Any]) -> str:
        """Publish a message to a Redis Stream.

        Args:
            channel: Stream name (e.g., 'stream:market_snapshots').
            message: Message payload. Must be JSON-serializable.

        Returns:
            Redis stream entry ID (e.g., '1234567890-0').
        """
        payload = orjson.dumps(message)
        entry_id: bytes = await self._redis.xadd(
            channel,
            {"data": payload},
            maxlen=self._max_len,
            approximate=True,
        )
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()


class RedisConsumer(Consumer):
    """Consume messages from Redis Streams using consumer groups.

    Provides at-least-once delivery semantics via consumer groups.
    Messages must be acknowledged after successful processing.

    Args:
        redis_url: Redis connection URL.
        block_ms: Milliseconds to block when waiting for new messages.
        batch_size: Maximum messages to read per XREADGROUP call.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        block_ms: int = 5000,
        batch_size: int = 10,
    ) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=False,
        )
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._channels: list[str] = []
        self._group = ""
        self._consumer_name = ""

    async def subscribe(
        self,
        channels: list[str],
        group: str,
        consumer_name: str,
    ) -> None:
        """Subscribe to channels, creating consumer groups if needed.

        Args:
            channels: Stream names to consume from.
            group: Consumer group name.
            consumer_name: Unique consumer name within the group.
        """
        self._channels = channels
        self._group = group
        self._consumer_name = consumer_name

        for channel in channels:
            try:
                await self._redis.xgroup_create(
                    channel,
                    group,
                    id="0",
                    mkstream=True,
                )
            except aioredis.ResponseError as e:
                # Group already exists — safe to ignore
                if "BUSYGROUP" not in str(e):
                    raise

    async def listen(self) -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
        """Yield messages from subscribed streams.

        Yields:
            Tuples of (channel_name, message_id, parsed_payload).
        """
        streams = {ch: ">" for ch in self._channels}

        while True:
            results: list[Any] = await self._redis.xreadgroup(
                groupname=self._group,
                consumername=self._consumer_name,
                streams=streams,
                count=self._batch_size,
                block=self._block_ms,
            )

            if not results:
                continue

            for stream_data in results:
                stream_name_raw, messages = stream_data
                stream_name = (
                    stream_name_raw.decode()
                    if isinstance(stream_name_raw, bytes)
                    else str(stream_name_raw)
                )

                for msg_id_raw, fields in messages:
                    msg_id = (
                        msg_id_raw.decode()
                        if isinstance(msg_id_raw, bytes)
                        else str(msg_id_raw)
                    )
                    raw_data = fields.get(b"data") or fields.get("data")
                    if raw_data is None:
                        continue
                    payload: dict[str, Any] = orjson.loads(raw_data)
                    yield stream_name, msg_id, payload

    async def ack(self, channel: str, group: str, message_id: str) -> None:
        """Acknowledge a message as processed.

        Args:
            channel: Stream name.
            group: Consumer group name.
            message_id: Stream entry ID.
        """
        await self._redis.xack(channel, group, message_id)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()
