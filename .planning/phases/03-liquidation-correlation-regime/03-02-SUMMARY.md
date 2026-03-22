---
phase: 03-liquidation-correlation-regime
plan: 02
subsystem: signals
tags: [correlation, basis-analysis, multi-window, funding-rate, portfolio-routing]

requires:
  - phase: 01-foundation-tuning
    provides: "Per-instrument YAML config, strategy matrix, FeatureStore timestamps"
provides:
  - "Multi-window basis z-score analysis (short=30, medium=60, long=120)"
  - "Funding rate confirmation for 2-of-3 window agreement"
  - "Portfolio A routing for high-conviction correlation signals"
affects: [05-cross-cutting, alpha-combiner]

tech-stack:
  added: []
  patterns: ["multi-window consensus gating", "funding rate as confirming factor"]

key-files:
  created: []
  modified:
    - agents/signals/strategies/correlation.py
    - agents/signals/tests/test_correlation.py
    - configs/strategies/correlation.yaml

key-decisions:
  - "Multi-window consensus: 3/3 fires always, 2/3 requires funding confirmation, <2 no basis signal"
  - "Funding rate direction: positive funding = bearish pressure, negative = bullish pressure"
  - "Portfolio A conviction threshold at 0.70 for correlation strategy"
  - "Per-instrument long lookbacks: BTC=150, QQQ/SPY=160, others use default 120"

patterns-established:
  - "Multi-window consensus: compute indicator at multiple lookbacks, require N-of-M agreement"
  - "Funding rate as confirmation factor: use latest funding_rates[-1] with len() > 0 guard"

requirements-completed: [CORR-01, CORR-02, CORR-03]

duration: 4min
completed: 2026-03-22
---

# Phase 03 Plan 02: Correlation Strategy Improvements Summary

**Multi-window basis analysis with 3-lookback consensus, funding rate confirmation for 2/3 agreement, and Portfolio A routing at 0.70 conviction**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T10:02:33Z
- **Completed:** 2026-03-22T10:06:46Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- Replaced single basis_lookback with three-window analysis (short=30, medium=60, long=120) for stronger consensus signals
- Added funding rate integration: 2/3 window agreement requires funding confirmation, 3/3 fires regardless (D-05, D-06)
- Funding rate alignment boosts conviction by configurable 0.10 (D-07)
- High-conviction signals (>= 0.70) route to Portfolio A for autonomous execution (D-10)
- Signal metadata now includes windows_agreed count and funding_confirms boolean

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests** - `b9f077d` (test)
2. **Task 1 (GREEN): Implementation** - `6724711` (feat)

_TDD task with RED/GREEN commits._

## Files Created/Modified
- `agents/signals/strategies/correlation.py` - Multi-window basis, funding integration, Portfolio A routing
- `agents/signals/tests/test_correlation.py` - 10+ new tests for multi-window, funding, and routing behavior
- `configs/strategies/correlation.yaml` - Three-window lookbacks, funding_rate_boost, portfolio_a_min_conviction per instrument

## Decisions Made
- Multi-window consensus: 3/3 fires always, 2/3 requires funding confirmation, <2 no basis signal
- Funding rate direction mapping: positive funding = bearish (longs pay shorts), negative = bullish
- Portfolio A conviction threshold at 0.70 for correlation (same as other strategies)
- Per-instrument long lookbacks scaled proportionally from old basis_lookback: BTC=150, QQQ/SPY=160

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated existing tests for new params and bar counts**
- **Found during:** Task 1 (GREEN implementation)
- **Issue:** Existing tests used old `basis_lookback` param and 80-bar stores, insufficient for new min_history=130
- **Fix:** Replaced basis_lookback with three-window params, increased default bar counts to 140, updated metadata assertions
- **Files modified:** agents/signals/tests/test_correlation.py
- **Verification:** All 35 tests pass (existing + new)
- **Committed in:** 6724711 (GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary to maintain backward compatibility of existing tests. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Correlation strategy now uses multi-window consensus with funding confirmation
- Ready for Phase 03 Plan 03 (regime trend improvements) or Phase 05 cross-cutting quality
- Alpha combiner may benefit from windows_agreed metadata for signal quality scoring

---
*Phase: 03-liquidation-correlation-regime*
*Completed: 2026-03-22*
