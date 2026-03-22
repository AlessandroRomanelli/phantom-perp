---
phase: 06-config-state-foundation
plan: 01
subsystem: config
tags: [dataclass, yaml, instruments, registry, decimal]

# Dependency graph
requires: []
provides:
  - InstrumentConfig frozen dataclass with ws_product_id property
  - Module-level instrument registry with load/get/list functions
  - instruments list in default.yaml with 5 perp contracts
  - get_settings() auto-populates instrument registry at startup
  - Removed hardcoded INSTRUMENT_ID, TICK_SIZE, MIN_ORDER_SIZE defaults
affects: [06-02, 07-ws-ingestion, 08-rest-polling]

# Tech tracking
tech-stack:
  added: []
  patterns: [instrument-registry-lookup, yaml-float-to-decimal-via-str, explicit-instrument-params]

key-files:
  created:
    - libs/common/instruments.py
    - libs/common/tests/test_instruments.py
  modified:
    - configs/default.yaml
    - libs/common/config.py
    - libs/common/constants.py
    - libs/common/utils.py
    - libs/coinbase/rest_client.py
    - libs/common/__init__.py

key-decisions:
  - "Load instruments from default.yaml directly in get_settings() since env-specific configs (paper, live) override other settings but do not duplicate the instruments list"
  - "Convert YAML floats to Decimal via Decimal(str(value)) to avoid floating point precision issues"

patterns-established:
  - "Instrument lookup: get_instrument(instrument_id) returns InstrumentConfig with tick_size, min_order_size"
  - "No instrument defaults: all callers must pass explicit instrument_id, tick_size, min_order_size"
  - "YAML-to-Decimal: always use Decimal(str(yaml_float)) pattern"

requirements-completed: [MCFG-01, MCFG-02]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 06 Plan 01: Instrument Config Registry Summary

**InstrumentConfig registry loading 5 perp contracts from YAML with per-instrument tick_size/min_order_size, wired into startup, hardcoded defaults removed**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T14:55:42Z
- **Completed:** 2026-03-22T14:59:56Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments
- Created InstrumentConfig frozen dataclass with ws_product_id property and module-level registry
- Added instruments list to default.yaml with all 5 perp contracts (ETH, BTC, SOL, QQQ, SPY)
- Wired load_instruments() into get_settings() for automatic registry population at startup
- Removed 6 hardcoded instrument constants from constants.py (INSTRUMENT_ID, BASE_CURRENCY, QUOTE_CURRENCY, TICK_SIZE, MIN_ORDER_SIZE, ACTIVE_INSTRUMENT_IDS)
- Made round_to_tick(), round_size(), get_orderbook(), get_candles(), get_funding_rate() require explicit instrument params

## Task Commits

Each task was committed atomically:

1. **Task 1: Create InstrumentConfig registry and tests** - `829257e` (feat, TDD)
2. **Task 2: Update default.yaml and wire registry into get_settings()** - `d1290a3` (feat)
3. **Task 3: Remove constant defaults from utils.py and rest_client.py** - `2f36f9d` (refactor)

## Files Created/Modified
- `libs/common/instruments.py` - InstrumentConfig dataclass and registry functions (new)
- `libs/common/tests/test_instruments.py` - 7 tests for registry (new)
- `configs/default.yaml` - Replaced singular instrument: with instruments: list (5 entries)
- `libs/common/config.py` - Added load_instruments() call in get_settings()
- `libs/common/constants.py` - Removed 6 instrument constants
- `libs/common/utils.py` - Removed TICK_SIZE/MIN_ORDER_SIZE default params
- `libs/coinbase/rest_client.py` - Removed INSTRUMENT_ID default params
- `libs/common/__init__.py` - Removed INSTRUMENT_ID re-export

## Decisions Made
- Load instruments from default.yaml directly (not env-specific config) since paper.yaml and live.yaml override execution/confirmation settings but don't duplicate the canonical instruments list
- Convert YAML floats to Decimal via Decimal(str(value)) to avoid floating point precision loss

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed libs/common/__init__.py re-exporting removed INSTRUMENT_ID**
- **Found during:** Task 3 (Remove constant defaults)
- **Issue:** `libs/common/__init__.py` imported and re-exported `INSTRUMENT_ID` from constants, causing ImportError after removal
- **Fix:** Removed INSTRUMENT_ID from __init__.py imports and __all__
- **Files modified:** libs/common/__init__.py
- **Verification:** `from libs.common.utils import round_to_tick` succeeds
- **Committed in:** 2f36f9d (Task 3 commit)

**2. [Rule 3 - Blocking] Fixed load_instruments() getting empty config in paper mode**
- **Found during:** Task 2 (Wire registry into get_settings)
- **Issue:** get_settings() passes env-specific yaml_config (paper.yaml) which has no instruments key, so registry was empty
- **Fix:** Always load default.yaml for instruments since it's the canonical source
- **Files modified:** libs/common/config.py
- **Verification:** `get_settings(); get_active_instrument_ids()` returns 5 IDs
- **Committed in:** d1290a3 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both fixes necessary for correct operation. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- InstrumentConfig registry is ready for Plan 02 caller migrations
- Callers in ingestion, signals, execution, reconciliation, and risk still import removed constants (Plan 02 scope)
- Full test suite will not pass until Plan 02 fixes all caller imports

---
*Phase: 06-config-state-foundation*
*Completed: 2026-03-22*
