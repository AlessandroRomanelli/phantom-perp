---
phase: 05-cross-cutting-quality
plan: 01
subsystem: signals
tags: [adaptive-conviction, swing-points, session-classifier, conviction-normalizer, scipy, numpy]

# Dependency graph
requires:
  - phase: 04-new-strategies
    provides: "Shared utility pattern (funding_filter.py) and FeatureStore data model"
provides:
  - "compute_adaptive_threshold -- volatility-percentile conviction scaling"
  - "find_swing_low / find_swing_high -- structure-aware stop detection"
  - "classify_session / SessionType -- UTC timestamp to session classification"
  - "normalize_conviction / should_route_portfolio_a -- conviction band mapping and Portfolio A routing"
  - "PORTFOLIO_A_UNIFIED_THRESHOLD = 0.70 constant"
affects: [05-02, 05-03, momentum, mean-reversion, correlation, regime-trend, orderbook-imbalance, vwap]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Shared utility modules: frozen dataclass result, no class state, no side effects"]

key-files:
  created:
    - agents/signals/adaptive_conviction.py
    - agents/signals/swing_points.py
    - agents/signals/session_classifier.py
    - agents/signals/conviction_normalizer.py
    - agents/signals/tests/test_adaptive_conviction.py
    - agents/signals/tests/test_swing_points.py
    - agents/signals/tests/test_session_classifier.py
    - agents/signals/tests/test_conviction_normalizer.py
  modified: []

key-decisions:
  - "Swing points extracted verbatim from momentum strategy -- identical logic, no self parameter"
  - "Conviction normalizer uses identity mapping (normalized = raw) per D-05 overlay design"
  - "PORTFOLIO_A_UNIFIED_THRESHOLD = 0.70 as single cross-strategy routing constant"

patterns-established:
  - "Shared utility pattern: function + frozen dataclass result (funding_filter, adaptive_conviction, swing_points, session_classifier, conviction_normalizer)"

requirements-completed: [XQ-01, XQ-02, XQ-04, XQ-05]

# Metrics
duration: 3min
completed: 2026-03-22
---

# Phase 05 Plan 01: Shared Utilities Summary

**Four shared utility modules (adaptive conviction, swing points, session classifier, conviction normalizer) with frozen dataclass results and 32 tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-22T12:25:52Z
- **Completed:** 2026-03-22T12:28:18Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Adaptive conviction threshold scaling via ATR volatility percentile (scipy.stats.percentileofscore)
- Swing point detection extracted from momentum strategy as standalone shared module
- Session classifier mapping UTC timestamps to crypto weekend / equity hours / crypto weekday
- Conviction normalizer with band mapping and unified Portfolio A routing threshold

## Task Commits

Each task was committed atomically:

1. **Task 1: Create adaptive conviction and swing point utilities with tests** - `4956034` (feat)
2. **Task 2: Create session classifier and conviction normalizer with tests** - `37b1597` (feat)

## Files Created/Modified
- `agents/signals/adaptive_conviction.py` - Volatility-percentile conviction threshold scaling
- `agents/signals/swing_points.py` - Swing high/low detection for structure-aware stops
- `agents/signals/session_classifier.py` - UTC timestamp to session type classification
- `agents/signals/conviction_normalizer.py` - Conviction band mapping and Portfolio A routing
- `agents/signals/tests/test_adaptive_conviction.py` - 6 tests for adaptive threshold
- `agents/signals/tests/test_swing_points.py` - 6 tests for swing point detection
- `agents/signals/tests/test_session_classifier.py` - 9 tests for session classification
- `agents/signals/tests/test_conviction_normalizer.py` - 11 tests for conviction normalization

## Decisions Made
- Swing points extracted verbatim from momentum strategy -- identical logic without self parameter
- Conviction normalizer uses identity mapping (normalized = raw) per D-05 overlay design
- PORTFOLIO_A_UNIFIED_THRESHOLD = 0.70 as single cross-strategy routing constant

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 4 shared utilities ready for integration in plans 05-02 and 05-03
- No cross-dependencies between the 4 modules (clean Wave 1)
- Pattern consistent with existing funding_filter.py utility

---
*Phase: 05-cross-cutting-quality*
*Completed: 2026-03-22*
