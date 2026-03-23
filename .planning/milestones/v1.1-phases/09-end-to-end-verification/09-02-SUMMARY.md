---
phase: 09-end-to-end-verification
plan: 02
subsystem: testing, dashboard
tags: [feature-store, multi-instrument, routing, dashboard, redis-streams]

requires:
  - phase: 08-rest-polling-multi-instrument
    provides: Multi-instrument REST polling for candles and funding rates
provides:
  - Multi-instrument FeatureStore routing tests (4 tests, all 5 instruments)
  - Per-instrument dashboard sections (snapshot table + FeatureStore status)
affects: []

tech-stack:
  added: []
  patterns:
    - "Per-instrument FeatureStore routing pattern: stores.get(snapshot.instrument)"
    - "Dashboard per-instrument grouping via xrevrange with first-seen dedup"

key-files:
  created: []
  modified:
    - agents/signals/tests/test_feature_store.py
    - agents/signals/tests/test_main.py
    - scripts/dashboard.py

key-decisions:
  - "_snap helper gets optional instrument param with backward-compatible default"
  - "FeatureStore status section reads from Redis hash, ready for future signals agent publication"

patterns-established:
  - "Multi-instrument test pattern: dict comprehension of FeatureStores with sample_interval=0"

requirements-completed: [ME2E-02]

duration: 3min
completed: 2026-03-23
---

# Phase 09 Plan 02: Multi-Instrument FeatureStore Routing Tests and Dashboard Summary

**4 integration tests verify per-instrument FeatureStore routing for all 5 contracts, plus dashboard per-instrument snapshot table and FeatureStore status sections**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-23T10:36:26Z
- **Completed:** 2026-03-23T10:39:00Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Updated _snap helper with optional instrument parameter for flexible test snapshot creation
- Added 4 routing tests covering all 5 instruments: correct routing, isolation, unknown skip, accumulation
- Dashboard shows per-instrument snapshot table with mark price, spread, funding, age, and OK/STALE/DOWN status
- Dashboard has FeatureStore sample count section ready for signals agent data

## Task Commits

Each task was committed atomically:

1. **Task 1: Update _snap helper and add multi-instrument FeatureStore routing tests** - `3c860f6` (test)
2. **Task 2: Add per-instrument dashboard sections** - `a6159d2` (feat)

## Files Created/Modified
- `agents/signals/tests/test_feature_store.py` - _snap helper now accepts instrument parameter
- `agents/signals/tests/test_main.py` - TestMultiInstrumentRouting class with 4 tests
- `scripts/dashboard.py` - Per-instrument snapshot table and FeatureStore status sections

## Decisions Made
- _snap helper gets optional instrument parameter with backward-compatible default of TEST_INSTRUMENT_ID
- FeatureStore status section reads from phantom:feature_store_status Redis hash -- ready for future signals agent publication without requiring it now

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ME2E-02 verification complete: FeatureStore routing proven for all 5 instruments
- Dashboard ready to show live per-instrument status once pipeline is running
- All 22 tests pass (existing + 4 new routing tests)

---
*Phase: 09-end-to-end-verification*
*Completed: 2026-03-23*
