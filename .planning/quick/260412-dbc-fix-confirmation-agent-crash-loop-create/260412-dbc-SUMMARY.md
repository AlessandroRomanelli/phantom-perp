---
plan: 260412-dbc
subsystem: libs/messaging
tags: [bug-fix, redis, confirmation-agent, crash-recovery]
key-files:
  modified:
    - libs/messaging/redis_streams.py
    - libs/messaging/tests/test_redis_streams.py
decisions:
  - Log stream_group_recreated warning before calling xgroup_create so the log fires even when the group already exists (BUSYGROUP path)
  - Extract _recreate_group() helper to avoid duplicating NOGROUP recovery logic across listen() and _reclaim_channel()
metrics:
  completed: "2026-04-12"
  tasks: 3
  files_modified: 2
---

# Quick Fix: NOGROUP crash-loop recovery in RedisConsumer

**One-liner:** Self-healing NOGROUP recovery in `RedisConsumer.listen()` and `_reclaim_channel()` that recreates evicted Redis stream groups instead of crashing.

## What was done

### Task 1 — `listen()` NOGROUP handling
Wrapped the `xreadgroup` call in `listen()` with a `try/except aioredis.ResponseError`. When the error message contains `"NOGROUP"`, the handler calls `_recreate_group()` for every subscribed channel and `continue`s the loop to retry. Non-NOGROUP errors re-raise as before.

### Task 2 — `_reclaim_channel()` NOGROUP handling + `_recreate_group()` helper
Added a new `_recreate_group(channel)` method that:
- Emits a `stream_group_recreated` warning log (before attempting the create, so it fires on both the fresh-create and the BUSYGROUP paths)
- Calls `xgroup_create` with `mkstream=True`
- Silently ignores `BUSYGROUP` (group already exists — safe)

Wrapped the `xautoclaim` call in `_reclaim_channel()` identically: on `NOGROUP`, call `_recreate_group()` and `return` to skip the reclaim cycle.

### Task 3 — Tests (3 new test cases, 19 total, all green)
- `test_listen_recovers_from_nogroup`: patches `xreadgroup` to raise NOGROUP on the first call, verifies the second call succeeds and a message is yielded
- `test_listen_nogroup_logs_warning`: same setup, asserts `stream_group_recreated` warning is captured with correct `channel` and `group` fields
- `test_reclaim_channel_recovers_from_nogroup`: patches `xautoclaim` to raise NOGROUP, verifies `_reclaim_channel()` returns without error and logs the warning

## Commits

| Hash | Message |
|------|---------|
| ee9549d | fix(messaging): handle NOGROUP in listen() and _reclaim_channel() via _recreate_group() |
| d5c8a14 | test(messaging): add NOGROUP recovery tests; move warning before xgroup_create in _recreate_group |

## Deviations from Plan

**1. [Rule 1 - Bug] Warning log moved before xgroup_create**
- **Found during:** Task 3 (test failure)
- **Issue:** Warning was inside the `try` block after `xgroup_create`, so it was never emitted when the group already existed (BUSYGROUP silently suppressed both the create and the log)
- **Fix:** Moved warning to before the `try` block so it fires on every NOGROUP recovery path
- **Files modified:** `libs/messaging/redis_streams.py`
- **Commit:** d5c8a14

## Self-Check: PASSED

- `libs/messaging/redis_streams.py` — exists, modified
- `libs/messaging/tests/test_redis_streams.py` — exists, modified
- Commit `ee9549d` — present
- Commit `d5c8a14` — present
- All 19 tests pass
