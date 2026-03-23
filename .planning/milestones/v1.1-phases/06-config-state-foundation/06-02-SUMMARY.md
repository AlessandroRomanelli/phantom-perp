---
phase: 06-config-state-foundation
plan: 02
subsystem: config
tags: [instruments, registry, migration, constants-removal, tick-size, min-order-size]

# Dependency graph
requires:
  - InstrumentConfig registry with get_instrument(), get_all_instruments(), get_active_instrument_ids()
  - round_to_tick() and round_size() require explicit params (no defaults)
  - get_orderbook(), get_candles(), get_funding_rate() require explicit instrument_id
provides:
  - IngestionState with required instrument_id field
  - Per-instrument state dict in ingestion main.py
  - All callers migrated from hardcoded constants to config-driven instrument lookups
  - Zero imports of removed constants (INSTRUMENT_ID, TICK_SIZE, MIN_ORDER_SIZE, ACTIVE_INSTRUMENT_IDS) in codebase
affects: [07-ws-ingestion, 08-rest-polling]

# Tech tracking
tech-stack:
  added: []
  patterns: [per-instrument-state-dict, tick-size-from-registry, explicit-instrument-params-in-strategies]

key-files:
  created:
    - agents/signals/tests/conftest.py
  modified:
    - agents/ingestion/state.py
    - agents/ingestion/normalizer.py
    - agents/ingestion/main.py
    - agents/ingestion/sources/ws_market_data.py
    - agents/ingestion/sources/candles.py
    - agents/ingestion/sources/funding_rate.py
    - agents/signals/main.py
    - agents/signals/strategies/momentum.py
    - agents/signals/strategies/mean_reversion.py
    - agents/signals/strategies/correlation.py
    - agents/signals/strategies/regime_trend.py
    - agents/signals/strategies/liquidation_cascade.py
    - agents/signals/strategies/orderbook_imbalance.py
    - agents/signals/strategies/vwap.py
    - agents/execution/main.py
    - agents/execution/algo_selector.py
    - agents/execution/stop_loss_manager.py
    - agents/execution/retry_handler.py
    - agents/risk/main.py
    - agents/risk/position_sizer.py
    - agents/reconciliation/main.py

key-decisions:
  - "Strategy tick_size lookup via get_instrument(snapshot.instrument).tick_size at evaluate() entry point"
  - "Test files use string literal TEST_INSTRUMENT_ID instead of depending on instrument registry"
  - "Added signals/tests/conftest.py with load_instruments to support strategy tests that call get_instrument()"

patterns-established:
  - "Strategy tick_size: every strategy evaluate() starts with tick_size = get_instrument(snapshot.instrument).tick_size"
  - "Test instrument: TEST_INSTRUMENT_ID = 'ETH-PERP' as module-level constant in each test file"

requirements-completed: [MSTA-01, MSTA-02]

# Metrics
duration: 22min
completed: 2026-03-22
---

# Phase 06 Plan 02: Caller Migration Summary

**Migrated all ~30 caller files from hardcoded instrument constants to config-driven InstrumentConfig registry with per-instrument tick_size, min_order_size, and ws_product_id lookups**

## Performance

- **Duration:** 22 min
- **Started:** 2026-03-22T15:02:28Z
- **Completed:** 2026-03-22T15:24:27Z
- **Tasks:** 3
- **Files modified:** 44

## Accomplishments
- Added instrument_id field to IngestionState and created per-instrument state dict in ingestion main.py
- Migrated all 7 strategy files to look up tick_size from InstrumentConfig registry
- Migrated execution (algo_selector, stop_loss_manager, retry_handler) to accept tick_size parameter
- Migrated risk engine to use idea.instrument for instrument lookup (min_order_size, tick_size)
- Updated all 17 test files with string literals and registry loading
- Full test suite: 743 tests passing, zero removed-constant imports remain

## Task Commits

Each task was committed atomically:

1. **Task 1: Update IngestionState, normalizer, and ingestion sources** - `11f4fb8` (refactor)
2. **Task 2: Migrate all non-ingestion callers** - `44d4956` (refactor)
3. **Task 3: Update all test files and verify full suite green** - `1442992` (test)

## Files Created/Modified
- `agents/ingestion/state.py` - Added instrument_id: str as required first field
- `agents/ingestion/normalizer.py` - Uses state.instrument_id instead of INSTRUMENT_ID constant
- `agents/ingestion/main.py` - Creates dict[str, IngestionState] from get_all_instruments()
- `agents/ingestion/sources/ws_market_data.py` - Accepts ws_product_id parameter
- `agents/ingestion/sources/candles.py` - Accepts instrument_id parameter
- `agents/ingestion/sources/funding_rate.py` - Accepts instrument_id parameter
- `agents/signals/main.py` - Uses get_active_instrument_ids() instead of ACTIVE_INSTRUMENT_IDS
- `agents/signals/strategies/*.py` - All 7 strategies use get_instrument() for tick_size
- `agents/execution/algo_selector.py` - Accepts tick_size parameter
- `agents/execution/stop_loss_manager.py` - Accepts tick_size parameter
- `agents/execution/retry_handler.py` - Accepts tick_size parameter
- `agents/risk/main.py` - Uses get_instrument(idea.instrument) for min_order_size and tick_size
- `agents/risk/position_sizer.py` - Accepts min_order_size parameter
- `agents/reconciliation/main.py` - Removed unused INSTRUMENT_ID import
- `agents/signals/tests/conftest.py` - New: loads instrument registry for strategy tests

## Decisions Made
- Strategy tick_size lookup via get_instrument(snapshot.instrument).tick_size at evaluate() entry point -- keeps all strategies instrument-aware without changing the SignalStrategy interface
- Test files use string literal TEST_INSTRUMENT_ID = "ETH-PERP" instead of depending on instrument registry -- explicit and avoids test dependency on config
- Created signals/tests/conftest.py with load_instruments() to support strategy tests that internally call get_instrument()

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed test_enrichment.py IngestionState() calls missing instrument_id**
- **Found during:** Task 3 (Update all test files)
- **Issue:** test_enrichment.py was not listed in the plan but had 4 IngestionState() calls without instrument_id
- **Fix:** Added instrument_id="ETH-PERP" to all 4 IngestionState() construction calls
- **Files modified:** agents/ingestion/tests/test_enrichment.py
- **Verification:** Test suite passes
- **Committed in:** 1442992 (Task 3 commit)

**2. [Rule 3 - Blocking] Fixed round_to_tick() missing tick_size in all 7 strategy files**
- **Found during:** Task 3 (running full test suite)
- **Issue:** round_to_tick() now requires tick_size param but strategies called it with 1 arg
- **Fix:** Added get_instrument() import and tick_size lookup in each strategy's evaluate() method
- **Files modified:** All 7 strategy files in agents/signals/strategies/
- **Verification:** 743 tests pass
- **Committed in:** 1442992 (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both fixes necessary for test suite to pass. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 06 complete: InstrumentConfig registry created (Plan 01) and all callers migrated (Plan 02)
- Ready for Phase 07 (WS multi-instrument ingestion) and Phase 08 (REST multi-instrument polling)
- Per-instrument state dict exists in ingestion main.py but currently only uses ETH-PERP (Phase 7/8 scope)

---
*Phase: 06-config-state-foundation*
*Completed: 2026-03-22*
