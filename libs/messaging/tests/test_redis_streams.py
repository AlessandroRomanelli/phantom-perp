"""Tests for RedisConsumer PEL reclaim via XAUTOCLAIM."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import fakeredis.aioredis
import orjson
import pytest
from structlog.testing import capture_logs

from libs.messaging.redis_streams import RedisConsumer, RedisPublisher


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


# ── Publisher tests ─────────────────────────────────────────────────────


@pytest.fixture
def make_publisher(fake_redis: fakeredis.aioredis.FakeRedis):
    """Factory that creates a RedisPublisher backed by fake redis."""
    publishers: list[RedisPublisher] = []

    def _make(**kwargs: Any) -> RedisPublisher:
        with patch(
            "libs.messaging.redis_streams.aioredis.from_url",
            return_value=fake_redis,
        ):
            publisher = RedisPublisher(**kwargs)
        publishers.append(publisher)
        return publisher

    yield _make

    for p in publishers:
        asyncio.get_event_loop().run_until_complete(p.close())


@pytest.mark.asyncio
async def test_publish_success(
    make_publisher,
) -> None:
    """publish() returns a non-empty stream entry ID."""
    pub = make_publisher()
    entry_id = await pub.publish(STREAM, {"hello": "world"})
    assert isinstance(entry_id, str)
    assert len(entry_id) > 0


@pytest.mark.asyncio
async def test_publish_serializes_payload(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_publisher,
) -> None:
    """Published message stores orjson-serialized payload in 'data' field."""
    pub = make_publisher()
    await pub.publish(STREAM, {"key": "val"})

    entries = await fake_redis.xrange(STREAM)
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields[b"data"] == orjson.dumps({"key": "val"})


@pytest.mark.asyncio
async def test_publish_connection_error(
    make_publisher,
) -> None:
    """ConnectionError from Redis propagates to caller."""
    import redis.exceptions

    pub = make_publisher()

    with (
        patch.object(
            pub._redis,
            "xadd",
            side_effect=redis.exceptions.ConnectionError("gone"),
        ),
        pytest.raises(redis.exceptions.ConnectionError),
    ):
        await pub.publish(STREAM, {"key": "val"})


@pytest.mark.asyncio
async def test_publisher_close(
    make_publisher,
) -> None:
    """close() completes without error."""
    pub = make_publisher()
    await pub.close()


# ── Consumer lifecycle tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_creates_group(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """subscribe() creates consumer groups for each channel."""
    stream_a = "stream:test_a"
    stream_b = "stream:test_b"
    consumer = make_consumer(reclaim_idle_ms=60000)
    consumer._redis = fake_redis

    await consumer.subscribe([stream_a, stream_b], GROUP, "sub-test")

    groups_a = await fake_redis.xinfo_groups(stream_a)
    groups_b = await fake_redis.xinfo_groups(stream_b)
    assert any(g["name"] == GROUP.encode() for g in groups_a)
    assert any(g["name"] == GROUP.encode() for g in groups_b)

    await consumer.close()


@pytest.mark.asyncio
async def test_subscribe_ignores_busygroup(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """subscribe() ignores BUSYGROUP when the group already exists."""
    await fake_redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)

    consumer = make_consumer(reclaim_idle_ms=60000)
    consumer._redis = fake_redis

    # Should not raise even though group already exists
    await consumer.subscribe([STREAM], GROUP, "dup-test")

    await consumer.close()


@pytest.mark.asyncio
async def test_listen_yields_messages(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """listen() yields (channel, id, payload) tuples for new messages."""
    consumer = make_consumer(reclaim_idle_ms=60000, block_ms=100)
    consumer._redis = fake_redis

    await consumer.subscribe([STREAM], GROUP, "listen-test")

    payload = {"event": "test_event"}
    await fake_redis.xadd(STREAM, {"data": orjson.dumps(payload)})

    async def _get_first():
        async for channel, msg_id, data in consumer.listen():
            return channel, msg_id, data

    result = await asyncio.wait_for(_get_first(), timeout=5.0)
    assert result is not None
    channel, msg_id, data = result
    assert channel == STREAM
    assert isinstance(msg_id, str)
    assert data == payload

    await consumer.close()


@pytest.mark.asyncio
async def test_ack_calls_xack(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """After ack(), xpending shows 0 pending messages."""
    consumer = make_consumer(reclaim_idle_ms=60000, block_ms=100)
    consumer._redis = fake_redis

    await consumer.subscribe([STREAM], GROUP, "ack-test")

    await fake_redis.xadd(STREAM, {"data": orjson.dumps({"k": "v"})})

    async def _get_first():
        async for channel, msg_id, data in consumer.listen():
            return channel, msg_id, data

    result = await asyncio.wait_for(_get_first(), timeout=5.0)
    assert result is not None
    _, msg_id, _ = result

    await consumer.ack(STREAM, GROUP, msg_id)

    pending_info = await fake_redis.xpending(STREAM, GROUP)
    assert pending_info["pending"] == 0

    await consumer.close()


@pytest.mark.asyncio
async def test_consumer_close_without_subscribe(
    make_consumer,
) -> None:
    """close() succeeds even when subscribe() was never called."""
    consumer = make_consumer()
    assert consumer._reclaim_task is None
    await consumer.close()


# ── NOGROUP recovery tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_recovers_from_nogroup(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """listen() recreates the consumer group when NOGROUP is raised by xreadgroup."""
    import redis.exceptions
    from unittest.mock import AsyncMock

    consumer = make_consumer(reclaim_idle_ms=60000, block_ms=100)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "nogroup-test"

    # Seed a real message so listen() eventually yields something after recovery
    await fake_redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    await fake_redis.xadd(STREAM, {"data": orjson.dumps({"recovered": True})})

    # Simulate NOGROUP on the first xreadgroup call, then delegate to the real impl
    original_xreadgroup = fake_redis.xreadgroup
    call_count = 0

    async def _xreadgroup_side_effect(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise redis.exceptions.ResponseError("NOGROUP No such consumer group")
        return await original_xreadgroup(**kwargs)

    fake_redis.xreadgroup = _xreadgroup_side_effect  # type: ignore[method-assign]

    async def _get_first():
        async for channel, msg_id, data in consumer.listen():
            return channel, msg_id, data

    result = await asyncio.wait_for(_get_first(), timeout=5.0)
    assert result is not None
    _, _, data = result
    assert data == {"recovered": True}
    # xreadgroup was called twice: once raising NOGROUP, once succeeding
    assert call_count == 2


@pytest.mark.asyncio
async def test_listen_nogroup_logs_warning(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """listen() emits stream_group_recreated warning on NOGROUP recovery."""
    import redis.exceptions

    consumer = make_consumer(reclaim_idle_ms=60000, block_ms=100)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "nogroup-log-test"

    await fake_redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    await fake_redis.xadd(STREAM, {"data": orjson.dumps({"x": 1})})

    original_xreadgroup = fake_redis.xreadgroup
    call_count = 0

    async def _xreadgroup_side_effect(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise redis.exceptions.ResponseError("NOGROUP No such consumer group")
        return await original_xreadgroup(**kwargs)

    fake_redis.xreadgroup = _xreadgroup_side_effect  # type: ignore[method-assign]

    async def _get_first():
        async for channel, msg_id, data in consumer.listen():
            return channel, msg_id, data

    with capture_logs() as cap:
        await asyncio.wait_for(_get_first(), timeout=5.0)

    recreate_events = [e for e in cap if e.get("event") == "stream_group_recreated"]
    assert len(recreate_events) == 1
    assert recreate_events[0]["channel"] == STREAM
    assert recreate_events[0]["group"] == GROUP


@pytest.mark.asyncio
async def test_reclaim_channel_recovers_from_nogroup(
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_consumer,
) -> None:
    """_reclaim_channel() recreates the group and returns gracefully on NOGROUP."""
    import redis.exceptions

    # Set up a crashed message so xpending_range returns entries (idle_map non-empty)
    await _setup_crashed_message(fake_redis)

    consumer = make_consumer(reclaim_idle_ms=0, reclaim_batch_size=10)
    consumer._redis = fake_redis
    consumer._channels = [STREAM]
    consumer._group = GROUP
    consumer._consumer_name = "reclaim-nogroup-test"

    original_xautoclaim = fake_redis.xautoclaim

    async def _xautoclaim_nogroup(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise redis.exceptions.ResponseError("NOGROUP No such consumer group")

    fake_redis.xautoclaim = _xautoclaim_nogroup  # type: ignore[method-assign]

    with capture_logs() as cap:
        # Should not raise — NOGROUP is handled by recreating the group
        await consumer._reclaim_channel(STREAM)

    # The reclaim queue should be empty (cycle was skipped after recreation)
    assert consumer._reclaim_queue.empty()

    recreate_events = [e for e in cap if e.get("event") == "stream_group_recreated"]
    assert len(recreate_events) == 1
    assert recreate_events[0]["channel"] == STREAM

    # Restore so teardown doesn't fail
    fake_redis.xautoclaim = original_xautoclaim  # type: ignore[method-assign]
