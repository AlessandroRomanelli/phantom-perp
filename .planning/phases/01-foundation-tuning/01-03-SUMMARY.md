---
phase: 01-foundation-tuning
plan: 03
subsystem: config
tags: [yaml, per-instrument, tuning, strategy-matrix]

# Dependency graph
requires:
  - phase: 01-foundation-tuning/plan-02
    provides: Config validation, load_strategy_config_for_instrument, log_config_diff
provides:
  - Strategy matrix declaring per-instrument strategy enablement
  - Per-instrument parameter overrides for all 4 active strategies across 5 instruments
  - Lowered conviction thresholds for increased signal frequency
  - Tests verifying config correctness for all instruments
affects: [02-strategy-improvements, 03-new-strategies, 05-cross-cutting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Strategy matrix pattern: configs/strategy_matrix.yaml as single source of truth for enablement"
    - "Per-instrument YAML override pattern: instruments.<ID>.parameters merges over base"

key-files:
  created:
    - configs/strategy_matrix.yaml
    - agents/signals/tests/test_instrument_configs.py
  modified:
    - agents/signals/main.py
    - configs/strategies/mean_reversion.yaml
    - configs/strategies/correlation.yaml
    - configs/strategies/liquidation_cascade.yaml
    - configs/strategies/regime_trend.yaml

key-decisions:
  - "Strategy matrix controls per-instrument enablement, separate from per-strategy YAML enabled flag"
  - "Liquidation cascade disabled for QQQ/SPY (crypto-native phenomenon, D-11)"
  - "Correlation enabled for QQQ/SPY (basis divergence valid for equity perps)"
  - "All min_conviction values lowered to 0.30-0.40 range for more signal frequency (D-04)"
  - "Base regime_trend min_conviction lowered from 0.50 to 0.40"

patterns-established:
  - "Strategy matrix: configs/strategy_matrix.yaml declares which strategies run on which instruments"
  - "Matrix check runs before per-strategy YAML check in build_strategies_for_instrument"

requirements-completed: [TUNE-01, TUNE-02, TUNE-03, TUNE-04, TUNE-05]

# Metrics
duration: 3min
completed: 2026-03-21
---

# Phase 01 Plan 03: Strategy Matrix and Per-Instrument Tuning Summary

**Strategy matrix with per-instrument parameter overrides for 4 active strategies across 5 instruments, lowering thresholds for increased signal frequency**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-21T21:49:31Z
- **Completed:** 2026-03-21T21:52:17Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- Created strategy matrix (configs/strategy_matrix.yaml) declaring per-instrument enablement for all 5 strategies
- Added per-instrument parameter overrides to all 4 active strategies (mean_reversion, correlation, liquidation_cascade, regime_trend) for all relevant instruments
- Lowered min_conviction and signal thresholds across all instruments to increase signal frequency (D-04)
- Added missing ETH-PERP overrides to regime_trend (was using bare defaults per Pitfall 4)
- 26 tests verifying config correctness, validation, and threshold compliance

## Task Commits

Each task was committed atomically:

1. **Task 1: Create strategy matrix and integrate into main.py** - `f7e60d8` (feat)
2. **Task 2: Add per-instrument overrides to all active strategy configs** - `26e9006` (feat)
3. **Task 3: Test per-instrument config loading for all instruments** - `4d9d9f3` (test)

## Files Created/Modified
- `configs/strategy_matrix.yaml` - Per-instrument strategy enablement matrix
- `agents/signals/main.py` - Added load_strategy_matrix() and matrix filtering in build_strategies_for_instrument
- `configs/strategies/mean_reversion.yaml` - Added 5 instrument overrides with lowered thresholds
- `configs/strategies/correlation.yaml` - Added 5 instrument overrides with research-informed params
- `configs/strategies/liquidation_cascade.yaml` - Added 3 crypto instrument overrides, QQQ/SPY disabled
- `configs/strategies/regime_trend.yaml` - Added ETH-PERP, lowered base min_conviction, added min_conviction to all instruments
- `agents/signals/tests/test_instrument_configs.py` - 26 tests for config loading, validation, and thresholds

## Decisions Made
- Strategy matrix is a separate YAML file (not embedded in default.yaml) for cleaner separation of concerns
- Matrix global toggle is checked before per-strategy YAML enabled flag (both must agree)
- Liquidation cascade disabled for QQQ/SPY via both matrix exclusion and YAML enabled:false (belt and suspenders)
- Correlation enabled for QQQ/SPY because basis divergence is valid for equity perps
- All per-instrument min_conviction values set to 0.30-0.40 range (down from 0.40-0.55 defaults)
- SOL-PERP gets wider stop losses and more aggressive thresholds (thin orderbook, high volatility)
- QQQ/SPY get tighter bands and higher cooldowns (lower vol, equity-style parameters)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 5 instruments now have explicit, research-informed parameter overrides
- Strategy matrix provides clean enablement control for future strategy additions
- Phase 01 (Foundation and Per-Instrument Tuning) is now complete (all 3 plans done)
- Ready for Phase 02 (strategy improvements), Phase 03 (new strategies), or Phase 04 (VWAP feasibility)

---
*Phase: 01-foundation-tuning*
*Completed: 2026-03-21*
