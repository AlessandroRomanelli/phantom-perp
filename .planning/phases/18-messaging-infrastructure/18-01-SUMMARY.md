---
phase: 18-messaging-infrastructure
plan: 01
status: complete
started: 2026-04-08
completed: 2026-04-08
commits:
  - c0af49e: "test(18-01): add failing tests for PEL reclaim behavior (RED)"
  - 881fd32: "feat(18-01): implement PEL reclaim via XAUTOCLAIM background loop (GREEN)"
  - 33f6631: "refactor(18-01): remove unused structlog import in tests"
---

# Plan 18-01 Summary: PEL Reclaim via XAUTOCLAIM

## What Changed

### libs/messaging/redis_streams.py
- Added `reclaim_idle_ms` and `reclaim_batch_size` constructor params to `RedisConsumer`
- Added `_reclaim_loop()` background asyncio task launched on `subscribe()`
- Added `_reclaim_channel()` that calls `xpending_range()` then `xautoclaim()` to reclaim idle PEL messages
- Added `_build_idle_map()` module helper to extract original consumer IDs from pending entries
- Modified `listen()` to drain reclaim queue before each `xreadgroup()` call
- Modified `close()` to cancel reclaim task cleanly with `contextlib.suppress(CancelledError)`
- Each reclaim emits structured log: `event="pel_message_reclaimed"` with `original_consumer` and `message_id`

### libs/messaging/tests/test_redis_streams.py (NEW)
- 7 tests using fakeredis covering: idle message recovery, original consumer logging, background task lifecycle, config params, defaults, close cancellation, no-idle no-op

### configs/default.yaml
- Added `messaging.reclaim_idle_ms: 60000` and `messaging.reclaim_batch_size: 10`

## Test Results
- 7/7 reclaim tests pass
- 546 passed, 0 failures in full regression suite (excluding 1 pre-existing `test_config_cannot_exceed_hard_leverage_cap_a`)
- ruff check clean (1 pre-existing TC003 warning on AsyncIterator import)
