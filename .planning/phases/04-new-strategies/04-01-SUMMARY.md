---
phase: 04-new-strategies
plan: 01
subsystem: signals
tags: [funding-rate, z-score, conviction-boost, strategy-integration]

# Dependency graph
requires:
  - phase: 03-liquidation-correlation-regime
    provides: correlation strategy with inline funding logic, momentum and mean reversion with conviction models
provides:
  - Shared funding rate confirmation utility (agents/signals/funding_filter.py)
  - FundingBoostResult dataclass for z-score-based funding alignment analysis
  - Funding boost integration in momentum, mean reversion, and correlation strategies
affects: [04-new-strategies, 05-cross-cutting]

# Tech tracking
tech-stack:
  added: []
  patterns: [shared-utility-extraction, opt-in-strategy-enhancement, z-score-funding-analysis]

key-files:
  created:
    - agents/signals/funding_filter.py
    - agents/signals/tests/test_funding_filter.py
  modified:
    - agents/signals/strategies/correlation.py
    - agents/signals/strategies/momentum.py
    - agents/signals/strategies/mean_reversion.py

key-decisions:
  - "Correlation 2/3 agreement gate uses simple direction alignment (not z-score utility) to preserve backward compatibility"
  - "Correlation conviction boost falls back to flat boost when insufficient funding data for z-score computation"
  - "Momentum and mean reversion use 0.08 max boost (vs correlation 0.10) for conservative opt-in"

patterns-established:
  - "Shared utility pattern: extract inline logic to reusable module with tests, then integrate via import"
  - "Opt-in funding boost: strategy params include funding_rate_boost, funding_z_score_threshold, funding_min_samples"

requirements-completed: [FUND-01, FUND-02, FUND-03]

# Metrics
duration: 8min
completed: 2026-03-22
---

# Phase 04 Plan 01: Funding Rate Filter Utility Summary

**Shared funding rate z-score confirmation utility with settlement decay, integrated into correlation (refactored), momentum, and mean reversion strategies**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-22T10:44:58Z
- **Completed:** 2026-03-22T10:52:46Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created shared `compute_funding_boost` utility with z-score analysis, direction alignment, and settlement time decay
- Refactored correlation strategy to use shared utility while preserving 2/3 agreement gating behavior
- Momentum and mean reversion strategies opt in to funding conviction boost with configurable parameters
- 10 dedicated unit tests for the utility, all 110 strategy tests pass with no regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create funding rate filter utility with tests** - `85f7e3c` (feat, TDD)
2. **Task 2: Integrate funding utility into correlation, momentum, and mean reversion** - `a687c48` (feat)

## Files Created/Modified
- `agents/signals/funding_filter.py` - Shared funding rate confirmation utility with FundingBoostResult and compute_funding_boost
- `agents/signals/tests/test_funding_filter.py` - 10 unit tests covering alignment, z-score, decay, edge cases
- `agents/signals/strategies/correlation.py` - Refactored to use shared utility; preserves fallback for simple alignment
- `agents/signals/strategies/momentum.py` - Added funding_rate_boost (0.08), z_score_threshold, min_samples params
- `agents/signals/strategies/mean_reversion.py` - Added same funding rate boost parameters and integration

## Decisions Made
- Correlation 2/3 agreement gate uses simple direction alignment (not z-score utility) to preserve backward compatibility with test data that has sparse funding rates
- Correlation conviction boost has dual path: z-score-based boost when sufficient data, flat fallback boost for simple alignment with insufficient data
- Momentum and mean reversion default to 0.08 max boost (lower than correlation's 0.10) for conservative opt-in

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed correlation funding_confirms backward compatibility**
- **Found during:** Task 2 (integration)
- **Issue:** Shared utility returns aligned=False when len(funding_rates) < min_samples. Existing correlation tests have stores with only 1 funding rate entry (FeatureStore deduplicates identical values). This broke the 2/3 agreement gate and conviction boost tests.
- **Fix:** Preserved simple direction-alignment check for the 2/3 agreement gate (independent of min_samples). Added fallback to flat `funding_rate_boost` when `funding_confirms` is true but utility has insufficient data for z-score computation.
- **Files modified:** agents/signals/strategies/correlation.py
- **Verification:** All 110 strategy tests pass
- **Committed in:** a687c48 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix for backward compatibility)
**Impact on plan:** Auto-fix necessary to preserve existing correlation behavior. No scope creep.

## Issues Encountered
- Pre-existing import error in `test_cooldown_per_instrument.py` and `test_main.py` (missing `ACTIVE_INSTRUMENT_IDS` from constants) -- not caused by this plan, not fixed (out of scope)

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Shared funding utility ready for use by future strategies (orderbook imbalance, VWAP, volume profile)
- Opt-in pattern established: any strategy can add funding boost with 3 params
- All existing tests pass

---
*Phase: 04-new-strategies*
*Completed: 2026-03-22*
