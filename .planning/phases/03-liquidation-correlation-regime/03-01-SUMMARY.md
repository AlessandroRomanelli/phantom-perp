---
phase: 03-liquidation-correlation-regime
plan: 01
subsystem: signals
tags: [liquidation-cascade, tiered-response, volume-surge, oi-drop, atr-stops]

requires:
  - phase: 01-foundation-tuning
    provides: per-instrument YAML config, strategy matrix, FeatureStore bar_volumes
provides:
  - Tiered cascade response with 3 severity levels (T1/T2/T3) and tier-specific stop/TP widths
  - Volume surge confirmation gate filtering organic OI reduction
  - Tier and vol_surge_ratio in signal metadata for downstream consumers
affects: [alpha-combiner, risk-agent, phase-05-cross-cutting]

tech-stack:
  added: []
  patterns:
    - "Tiered response pattern: classify event severity into tiers with tier-specific parameters"
    - "Volume surge gate: require bar_volume spike above average before generating signal"

key-files:
  created: []
  modified:
    - agents/signals/strategies/liquidation_cascade.py
    - agents/signals/tests/test_liquidation_cascade.py
    - configs/strategies/liquidation_cascade.yaml

key-decisions:
  - "Replaced single oi_drop_threshold_pct with tier1_min_oi_drop_pct for backward compat via property alias"
  - "Volume surge gate uses bar_volumes from FeatureStore (np.diff of volume_24h samples)"
  - "Tier conviction boost: T1=+0.0, T2=+0.05, T3=+0.10 additive to base conviction"
  - "SOL-PERP gets wider tier stops (T3: 3.5/5.0) due to thin book"

patterns-established:
  - "Tier classification via _classify_tier static method with boundary thresholds"
  - "Volume surge ratio as metadata field for downstream analysis"

requirements-completed: [LIQ-01, LIQ-02]

duration: 4min
completed: 2026-03-22
---

# Phase 03 Plan 01: Liquidation Cascade Improvements Summary

**Graduated 3-tier cascade response (T1/T2/T3) with tier-specific stop/TP widths and volume surge confirmation gate**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T10:02:32Z
- **Completed:** 2026-03-22T10:06:45Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- Replaced single OI drop threshold with 3-tier classification: T1 [2%,4%), T2 [4%,8%), T3 [8%+)
- Added tier-specific stop/TP ATR multipliers (T1: 1.5/2.0, T2: 2.0/3.0, T3: 3.0/4.5)
- Added volume surge confirmation gate requiring 1.5x average bar volume to filter organic OI reduction
- Added tier conviction boost so Tier 3 cascades produce higher baseline conviction
- Included tier and vol_surge_ratio in signal metadata for downstream consumers
- 24 tests passing (14 existing + 10 new)

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for tiers and volume surge** - `cdd5524` (test)
2. **Task 1 GREEN: Implement tiered cascade and volume surge** - `9d2142c` (feat)

## Files Created/Modified
- `agents/signals/strategies/liquidation_cascade.py` - Tiered cascade with _classify_tier, volume surge gate, tier-specific stop/TP
- `agents/signals/tests/test_liquidation_cascade.py` - 10 new tests for tier classification, boundaries, volume surge, metadata
- `configs/strategies/liquidation_cascade.yaml` - Tier thresholds, tier stop/TP mults, volume surge params, SOL-PERP wider stops

## Decisions Made
- Replaced `oi_drop_threshold_pct` with `tier1_min_oi_drop_pct`, added backward-compat property alias
- Volume surge uses `store.bar_volumes` (np.diff of volume_24h) consistent with momentum strategy pattern
- Tier conviction boost is additive (T1: +0.0, T2: +0.05, T3: +0.10) keeping base conviction model intact
- SOL-PERP gets wider tier stops (T3: 3.5/5.0 ATR) due to thin order book

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated existing tests for parameter rename**
- **Found during:** Task 1 GREEN
- **Issue:** Existing tests used `oi_drop_threshold_pct` parameter which was replaced by `tier1_min_oi_drop_pct`
- **Fix:** Updated all test parameter references to `tier1_min_oi_drop_pct`
- **Files modified:** agents/signals/tests/test_liquidation_cascade.py
- **Verification:** All 24 tests pass
- **Committed in:** 9d2142c

**2. [Rule 1 - Bug] Fixed volume pattern in test helper**
- **Found during:** Task 1 GREEN
- **Issue:** Test helper `_build_cascade_store` used constant volume_24h, producing zero bar_volume deltas
- **Fix:** Redesigned volume pattern with steady increments and configurable spike on final bar
- **Files modified:** agents/signals/tests/test_liquidation_cascade.py
- **Verification:** Volume surge tests correctly pass/reject based on surge ratio
- **Committed in:** 9d2142c

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness with the new volume surge gate. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Tiered cascade ready for downstream consumption (alpha combiner can read tier from metadata)
- Volume surge pattern established for reuse in other strategies
- Correlation (03-02) and regime trend (03-03) improvements can proceed independently

---
*Phase: 03-liquidation-correlation-regime*
*Completed: 2026-03-22*
