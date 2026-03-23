---
phase: 08-rest-polling-multi-instrument
plan: 01
subsystem: ingestion
tags: [rest-polling, multi-instrument, candles, funding-rate, staleness, error-isolation]

# Dependency graph
requires:
  - phase: 06-config-state-foundation
    provides: InstrumentConfig registry, per-instrument IngestionState dict
provides:
  - Per-instrument candle polling for all 5 instruments
  - Per-instrument funding rate polling for all 5 instruments
  - REST staleness detection with readiness flag reset
  - Error isolation between instrument pollers
  - Consecutive failure tracking with threshold warnings
affects: [09-e2e-verification]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_run_rest_poller_isolated wrapper for TaskGroup crash isolation"
    - "_mark_stale_rest_data periodic staleness checker (analogous to WS staleness)"
    - "Staggered poller startup via REST_POLLER_STAGGER_SECONDS delay"
    - "Per-instrument REST clients sharing one RateLimiter"

key-files:
  created:
    - agents/ingestion/tests/test_main_wiring.py
  modified:
    - agents/ingestion/main.py
    - agents/ingestion/state.py
    - agents/ingestion/sources/candles.py
    - agents/ingestion/sources/funding_rate.py
    - agents/ingestion/tests/test_candles.py
    - agents/ingestion/tests/test_funding_rate.py
    - libs/common/constants.py

key-decisions:
  - "Per-instrument REST clients with shared RateLimiter rather than one shared client"
  - "Staggered startup (2s between instruments) to avoid initial API burst"
  - "Error isolation via wrapper prevents one instrument crash from tearing down TaskGroup"

patterns-established:
  - "_run_rest_poller_isolated: wrap long-running coroutines to prevent TaskGroup teardown on crash"
  - "REST staleness thresholds: 10min for candles, 15min for funding"

requirements-completed: [MPOL-01, MPOL-02]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 08 Plan 01: Multi-Instrument REST Polling Summary

**Per-instrument candle and funding rate REST pollers for all 5 instruments with staggered starts, error isolation, consecutive failure tracking, and REST staleness detection**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T19:54:18Z
- **Completed:** 2026-03-22T19:58:37Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments
- Candle pollers now spawn for all 5 instruments (ETH, BTC, SOL, QQQ, SPY) instead of ETH-PERP only
- Funding rate pollers spawn for all 5 instruments with staggered 2-second delays
- Error isolation ensures one instrument's poller crash doesn't kill others
- REST staleness checker resets readiness flags when candle/funding data exceeds thresholds
- Consecutive failure tracking warns at 5 consecutive failures per poller

## Task Commits

Each task was committed atomically:

1. **Task 1: Add staleness timestamp and consecutive failure tracking** - `78d7361` (feat)
2. **Task 2: Refactor main.py for multi-instrument REST polling** - `5d47b51` (feat)
3. **Task 3: Add multi-instrument wiring tests** - `593883f` (test)

## Files Created/Modified
- `agents/ingestion/main.py` - Multi-instrument REST polling orchestration with error isolation and staleness checker
- `agents/ingestion/state.py` - Added last_candle_update timestamp field
- `agents/ingestion/sources/candles.py` - Consecutive failure tracking, instrument-aware logging, Coinbase Advanced naming
- `agents/ingestion/sources/funding_rate.py` - Consecutive failure tracking, instrument-aware logging, Coinbase Advanced naming
- `agents/ingestion/tests/test_candles.py` - Added last_candle_update and instrument_id forwarding tests
- `agents/ingestion/tests/test_funding_rate.py` - Added instrument_id forwarding test
- `agents/ingestion/tests/test_main_wiring.py` - 6 tests for staleness detection and error isolation
- `libs/common/constants.py` - REST_CANDLE_STALE_SECONDS, REST_FUNDING_STALE_SECONDS, REST_POLLER_STAGGER_SECONDS

## Decisions Made
- Per-instrument REST clients with shared RateLimiter rather than one shared client (isolates HTTP connection pools)
- Staggered startup (2s between instruments) to avoid initial API burst
- Error isolation via wrapper prevents one instrument crash from tearing down TaskGroup

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed remaining Coinbase INTX docstring references**
- **Found during:** Post-Task 2 verification
- **Issue:** run_all_candle_pollers and run_funding_poller inner docstrings still said "Coinbase INTX"
- **Fix:** Replaced with "Coinbase Advanced" in all remaining occurrences
- **Files modified:** agents/ingestion/sources/candles.py, agents/ingestion/sources/funding_rate.py
- **Committed in:** f62a2dd

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor docstring consistency fix. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 5 instruments now have REST candle and funding rate data flowing through ingestion
- Ready for Phase 09 end-to-end verification that snapshots for all instruments reach the signals agent
- WS multi-instrument subscription (Phase 07) already complete

---
*Phase: 08-rest-polling-multi-instrument*
*Completed: 2026-03-22*
