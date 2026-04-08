"""Tests for RedisConsumer PEL reclaim via XAUTOCLAIM."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import fakeredis.aioredis
import orjson
import pytest
from structlog.testing import capture_logs

from libs.messaging.redis_streams import RedisConsumer


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def make_consumer(fake_redis: fakeredis.aioredis.FakeRedis):
    """Factory that creates a RedisConsumer backed by fake redis."""
    consumers: list[RedisConsumer] = []

    def _make(**kwargs: Any) -> RedisConsumer:
        with patch(
            "libs.messaging.redis_streams.aioredis.from_url",
            return_value=fake_redis,
        ):
            consumer = RedisConsumer(**kwargs)
        consumers.append(consumer)
        return consumer

    yield _make

    for c in consumers:
        if hasattr(c, "_reclaim_task") and c._reclaim_task is not None:
            c._reclaim_task.cancel()


STREAM = "stream:test"
GROUP = "test-group"


async def _setup_crashed_message(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> str:
    """Add a message, read it as 'crashed-0' without acking. Returns message ID."""
    await fake_redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    msg_id = await fake_redis.xadd(
        STREAM, {"data": orjson.dumps({"key": "value"})}
    )
    await fake_redis.xreadgroup(
        groupname=GROUP,
        consumername="crashed-0",
        streams={STREAM: ">"},
        count=1,
    )
    return msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)


@pytest.mark.asyncio
async def test_reclaim_idle_message(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """Reclaimed idle messages are yielded to the active consumer."""
    msg_id = await _setup_crashed_message(fake_redis)

    consumer = make_consumer(reclaim_idle_ms=0, reclaim_batch_size=10)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "active-0"

    await consumer._reclaim_channel(STREAM)

    assert not consumer._reclaim_queue.empty()
    channel, reclaimed_id, payload = consumer._reclaim_queue.get_nowait()
    assert channel == STREAM
    assert reclaimed_id == msg_id
    assert payload == {"key": "value"}


@pytest.mark.asyncio
async def test_reclaim_logs_original_consumer(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """Reclaim emits structured log with original consumer ID."""
    await _setup_crashed_message(fake_redis)

    consumer = make_consumer(reclaim_idle_ms=0, reclaim_batch_size=10)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "active-0"

    with capture_logs() as cap:
        await consumer._reclaim_channel(STREAM)

    reclaim_events = [e for e in cap if e.get("event") == "pel_message_reclaimed"]
    assert len(reclaim_events) == 1
    assert reclaim_events[0]["original_consumer"] == "crashed-0"
    assert "message_id" in reclaim_events[0]


@pytest.mark.asyncio
async def test_reclaim_loop_background(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """subscribe() launches _reclaim_loop as a background asyncio task."""
    consumer = make_consumer(reclaim_idle_ms=60000)
    consumer._redis = fake_redis

    await consumer.subscribe([STREAM], GROUP, "bg-consumer")

    assert consumer._reclaim_task is not None
    assert isinstance(consumer._reclaim_task, asyncio.Task)
    consumer._reclaim_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer._reclaim_task


@pytest.mark.asyncio
async def test_reclaim_config_params(make_consumer) -> None:
    """Constructor stores custom reclaim parameters."""
    consumer = make_consumer(reclaim_idle_ms=30000, reclaim_batch_size=5)
    assert consumer._reclaim_idle_ms == 30000
    assert consumer._reclaim_batch_size == 5


@pytest.mark.asyncio
async def test_reclaim_default_config(make_consumer) -> None:
    """Constructor uses sensible defaults for reclaim parameters."""
    consumer = make_consumer()
    assert consumer._reclaim_idle_ms == 60_000
    assert consumer._reclaim_batch_size == 10


@pytest.mark.asyncio
async def test_reclaim_task_cancelled_on_close(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """close() cancels the reclaim background task."""
    consumer = make_consumer(reclaim_idle_ms=60000)
    consumer._redis = fake_redis

    await consumer.subscribe([STREAM], GROUP, "close-test")
    assert consumer._reclaim_task is not None

    await consumer.close()
    assert consumer._reclaim_task.cancelled()


@pytest.mark.asyncio
async def test_reclaim_no_idle_messages_yields_nothing(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """When no messages are idle, reclaim yields nothing and emits no logs."""
    await fake_redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    msg_id = await fake_redis.xadd(
        STREAM, {"data": orjson.dumps({"key": "value"})}
    )
    # Read AND ack — nothing pending
    await fake_redis.xreadgroup(
        groupname=GROUP,
        consumername="normal-0",
        streams={STREAM: ">"},
        count=1,
    )
    await fake_redis.xack(STREAM, GROUP, msg_id)

    consumer = make_consumer(reclaim_idle_ms=0, reclaim_batch_size=10)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "active-0"

    with capture_logs() as cap:
        await consumer._reclaim_channel(STREAM)

    assert consumer._reclaim_queue.empty()
    reclaim_events = [e for e in cap if e.get("event") == "pel_message_reclaimed"]
    assert len(reclaim_events) == 0
