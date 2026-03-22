---
phase: 04-new-strategies
plan: 03
subsystem: signals
tags: [vwap, volume-weighted, session-aware, mean-reversion, deviation]

requires:
  - phase: 01-foundation-tuning
    provides: FeatureStore with bar_volumes, volumes, timestamps; SignalSource.VWAP enum
provides:
  - VWAP deviation strategy with session-aware reset and time-of-session conviction
  - Per-instrument VWAP configs with crypto (00:00 UTC) and equity (14:00 UTC) session resets
  - Feasibility validation proving clamped bar_volumes produce usable VWAP anchors
affects: [05-cross-cutting, alpha-combiner]

tech-stack:
  added: []
  patterns: [session-aware-vwap, feasibility-gated-implementation, clamped-volume-weighting]

key-files:
  created:
    - agents/signals/strategies/vwap.py
    - agents/signals/tests/test_vwap.py
    - configs/strategies/vwap.yaml
  modified:
    - agents/signals/main.py
    - configs/strategy_matrix.yaml

key-decisions:
  - "Feasibility PASSES: clamped bar_volumes produce VWAP with std 0.17 vs price std 1.32 (8x smoother)"
  - "48% negative bar_volumes handled by clamping to 0; cumulative volume still grows"
  - "Both session-reset and rolling VWAP modes supported; session-reset is default"
  - "BTC-PERP gets higher deviation_threshold (2.5) due to lower relative volatility"
  - "SOL-PERP gets lower threshold (1.8) with higher min_conviction (0.45) for its volatility"

patterns-established:
  - "Feasibility-gated strategy: validate data quality programmatically before committing to implementation"
  - "Session-aware computation: configurable reset hour for crypto vs equity trading sessions"

requirements-completed: [VWAP-01, VWAP-02, VWAP-03, VWAP-04]

duration: 4min
completed: 2026-03-22
---

# Phase 04 Plan 03: VWAP Deviation Strategy Summary

**Session-aware VWAP deviation mean reversion strategy with feasibility-validated clamped volume weighting, configurable session resets (crypto 00:00 UTC, equity 14:00 UTC), and time-of-session conviction scaling**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T10:54:52Z
- **Completed:** 2026-03-22T10:58:52Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Validated VWAP feasibility programmatically: clamped bar_volumes produce anchor 8x smoother than raw price despite 48% negative values
- Implemented VWAPStrategy with session-aware reset, early session suppression, and 2-component conviction model (deviation + session progress)
- Registered VWAP across all 5 instruments with per-instrument session reset hours and deviation thresholds

## Task Commits

Each task was committed atomically:

1. **Task 1: VWAP feasibility validation and conditional implementation**
   - `657809f` (test: add failing tests for VWAP feasibility and strategy)
   - `4515072` (feat: implement VWAP deviation strategy with session awareness)
2. **Task 2: Register VWAP strategy** - `26cd0bb` (feat: register VWAP strategy with per-instrument configs)

## Files Created/Modified
- `agents/signals/strategies/vwap.py` - VWAP deviation strategy with session reset, clamped volume weighting, conviction model
- `agents/signals/tests/test_vwap.py` - 16 tests: 4 feasibility validation + 12 strategy tests
- `configs/strategies/vwap.yaml` - Per-instrument configs with session reset hours and thresholds
- `agents/signals/main.py` - VWAPStrategy and VWAPParams registration
- `configs/strategy_matrix.yaml` - VWAP enabled for all 5 instruments

## Decisions Made
- Feasibility validated programmatically (D-05): 48% negative bar_volumes clamped to 0, resulting VWAP is 8x smoother than price
- Both clamped-session and rolling-volume approaches pass stability test; session-reset mode is default
- BTC-PERP deviation threshold raised to 2.5 (more established price, less deviation per std)
- SOL-PERP threshold lowered to 1.8 but min_conviction raised to 0.45 (volatile, needs stronger filter)
- QQQ/SPY session reset at 14:00 UTC (approximate 09:30 ET market open)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Pre-existing import error in `agents/signals/tests/test_cooldown_per_instrument.py` and `test_main.py` referencing `ACTIVE_INSTRUMENT_IDS` constant that doesn't exist in committed constants.py. Not caused by this plan's changes; logged as out-of-scope.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- VWAP strategy complete and registered for all 5 instruments
- Phase 04 (New Strategies) now fully complete: funding arb, OBI, and VWAP all implemented
- Ready for Phase 05 cross-cutting quality improvements

---
*Phase: 04-new-strategies*
*Completed: 2026-03-22*
