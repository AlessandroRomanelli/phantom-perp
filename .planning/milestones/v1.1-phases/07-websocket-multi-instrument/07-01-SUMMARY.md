---
phase: 07-websocket-multi-instrument
plan: 01
subsystem: ingestion
tags: [websocket, multi-instrument, dispatch, readiness-gating, staleness-detection]

# Dependency graph
requires:
  - phase: 06-config-state-foundation
    provides: "Per-instrument IngestionState dict and InstrumentConfig registry"
provides:
  - "Multi-instrument WS dispatch via _dispatch_message routing by product ID"
  - "Readiness flags (has_ws_tick, has_candles, has_funding) gating snapshot publishing"
  - "Per-instrument 100ms snapshot throttle via time.monotonic()"
  - "Reconnect staleness detection resetting has_ws_tick after STALE_DATA_HALT_SECONDS"
  - "product_to_instrument mapping built from InstrumentConfig registry"
affects: [08-rest-multi-instrument, 09-e2e-verification]

# Tech tracking
tech-stack:
  added: []
  patterns: ["multi-instrument WS dispatch with product_to_instrument mapping", "readiness flag gating for snapshot publishing", "periodic staleness check after WS reconnect"]

key-files:
  created: []
  modified:
    - "agents/ingestion/state.py"
    - "agents/ingestion/sources/ws_market_data.py"
    - "agents/ingestion/main.py"
    - "agents/ingestion/sources/candles.py"
    - "agents/ingestion/sources/funding_rate.py"
    - "agents/ingestion/tests/test_ws_market_data.py"

key-decisions:
  - "Periodic staleness check every STALE_DATA_HALT_SECONDS in WS listen loop rather than event-driven after reconnect"
  - "Readiness flags set in source pollers (candles.py, funding_rate.py) rather than wrappers in main.py"

patterns-established:
  - "product_to_instrument: dict[str, str] mapping for WS dispatch routing"
  - "Readiness gating: is_ready() = has_ws_tick AND has_candles AND has_funding"
  - "_dispatch_message returns list of updated instrument_ids for callback invocation"

requirements-completed: [MWS-01, MWS-02]

# Metrics
duration: 3min
completed: 2026-03-22
---

# Phase 07 Plan 01: WebSocket Multi-Instrument Summary

**Multi-instrument WS dispatch routing all 5 products to per-instrument states with readiness gating, 100ms throttle, and reconnect staleness detection**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-22T17:47:02Z
- **Completed:** 2026-03-22T17:50:45Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Single WS connection subscribes to all 5 instrument product IDs via product_to_instrument mapping
- _dispatch_message routes incoming WS messages to correct per-instrument IngestionState by product ID
- Readiness flags (has_ws_tick, has_candles, has_funding) gate snapshot publishing via is_ready()
- Per-instrument 100ms snapshot throttle prevents flooding via time.monotonic()
- Reconnect staleness detection resets has_ws_tick for instruments not updated within 30s
- 32 WS market data tests passing (11 existing + 21 new), 78 total ingestion tests passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Add readiness flags and multi-instrument WS dispatch** - `a19344d` (test: RED) + `262b3ea` (feat: GREEN)
2. **Task 2: Wire multi-instrument WS dispatch in main.py** - `d4948d6` (feat)

## Files Created/Modified
- `agents/ingestion/state.py` - Added has_ws_tick, has_candles, has_funding readiness flags and is_ready() method
- `agents/ingestion/sources/ws_market_data.py` - Refactored for multi-instrument: _dispatch_message, _extract_product_ids, _mark_stale_instruments, new run_ws_market_data signature
- `agents/ingestion/main.py` - product_to_instrument mapping, on_ws_update(instrument_id), per-instrument throttle, readiness gate
- `agents/ingestion/sources/candles.py` - Sets has_candles=True on first successful poll
- `agents/ingestion/sources/funding_rate.py` - Sets has_funding=True on first successful poll
- `agents/ingestion/tests/test_ws_market_data.py` - Added TestReadinessFlags, TestMultiInstrumentDispatch, TestMultiInstrumentSubscribe, TestReconnectStaleness

## Decisions Made
- Periodic staleness check (every 30s in listen loop) rather than event-driven after reconnect -- simpler, lightweight (iterates 5 states), and catches gradual data loss
- Readiness flags set directly in candles.py/funding_rate.py source files (1-line each) rather than wrapper tasks in main.py -- cleaner, flag is set at the exact moment data arrives

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Multi-instrument WS dispatch complete and tested
- Phase 08 (REST multi-instrument polling) can add candle/funding pollers for BTC, SOL, QQQ, SPY
- Phase 09 (E2E verification) can verify all 5 instruments produce snapshots

---
*Phase: 07-websocket-multi-instrument*
*Completed: 2026-03-22*
