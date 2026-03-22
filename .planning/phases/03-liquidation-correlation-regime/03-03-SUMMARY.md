---
phase: 03-liquidation-correlation-regime
plan: 03
subsystem: signals
tags: [regime-trend, adaptive-thresholds, trailing-stop, percentileofscore, scipy]

requires:
  - phase: 01-foundation-tuning
    provides: "Per-instrument YAML configs, strategy matrix, FeatureStore timestamps"
  - phase: 02-momentum-mean-reversion
    provides: "percentileofscore pattern for volatility percentile computation"
provides:
  - "Volatility-adaptive ADX threshold scaling (0.8x-1.2x) for regime trend strategy"
  - "Volatility-adaptive ATR expansion threshold scaling (0.85x-1.15x)"
  - "Trailing stop metadata (trail_activation_pct, trail_distance_atr) in signal metadata"
  - "Tighter initial stop (1.8x ATR) when trail enabled for Portfolio B"
affects: [execution-agent, phase-05-cross-cutting]

tech-stack:
  added: []
  patterns: ["percentileofscore for volatility-adaptive threshold scaling", "trail metadata in signal metadata dict for downstream consumption"]

key-files:
  created: []
  modified:
    - agents/signals/strategies/regime_trend.py
    - agents/signals/tests/test_regime_trend.py
    - configs/strategies/regime_trend.yaml

key-decisions:
  - "Used same percentileofscore pattern from momentum strategy for volatility percentile computation"
  - "Trail metadata emitted as signal metadata keys for future execution layer consumption (no execution changes needed now)"
  - "QQQ/SPY trail disabled since Portfolio A already disabled for these instruments"

patterns-established:
  - "Adaptive threshold pattern: percentileofscore -> linear interpolation -> clamping"
  - "Trail metadata pattern: strategy emits trail params in metadata dict for downstream agents"

requirements-completed: [RT-01, RT-02]

duration: 4min
completed: 2026-03-22
---

# Phase 03 Plan 03: Regime Trend Improvements Summary

**Volatility-adaptive ADX/ATR thresholds via percentileofscore with trailing stop metadata and tighter initial stops**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T10:02:40Z
- **Completed:** 2026-03-22T10:06:17Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- ADX threshold now scales 0.8x-1.2x based on ATR volatility percentile, making trend entry easier in quiet markets and stricter in volatile ones
- ATR expansion threshold scales 0.85x-1.15x with same volatility-adaptive logic, with clamping at [0.8, 1.5]
- Trailing stop metadata (trail_activation_pct, trail_distance_atr) emitted in every signal for future execution layer consumption
- Portfolio B uses tighter initial stop (1.8x ATR vs 2.5x) when trail enabled, compensated by trailing stop protection
- Per-instrument overrides: SOL wider trail (2.0 ATR), BTC higher activation (1.5%), QQQ/SPY trail disabled

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Adaptive thresholds and trail metadata tests** - `f44f087` (test)
2. **Task 1 (GREEN): Implement adaptive thresholds and trail metadata** - `48d0574` (feat)

_TDD task with RED (failing tests) then GREEN (implementation) commits._

## Files Created/Modified
- `agents/signals/strategies/regime_trend.py` - Added _compute_adaptive_thresholds(), adaptive threshold params, trail metadata, tighter initial stop logic
- `agents/signals/tests/test_regime_trend.py` - Added 11 new tests: TestAdaptiveThresholds (8) + TestTrailingStopMetadata (3)
- `configs/strategies/regime_trend.yaml` - Added adaptive threshold and trail params with per-instrument overrides

## Decisions Made
- Used same percentileofscore pattern from momentum strategy (Phase 2) for consistency
- Trail metadata emitted as metadata dict keys rather than new StandardSignal fields (preserves signal interface constraint)
- QQQ/SPY trail disabled since Portfolio A already disabled for these slower-moving equity indices

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test assuming LONG direction**
- **Found during:** Task 1 (TDD GREEN)
- **Issue:** test_tighter_initial_stop_when_trail_enabled assumed first signal was LONG but could be SHORT due to random seed in data generation
- **Fix:** Changed test to use abs(entry - stop_loss) for direction-agnostic stop distance comparison
- **Files modified:** agents/signals/tests/test_regime_trend.py
- **Verification:** All 24 tests pass
- **Committed in:** 48d0574 (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix in test)
**Impact on plan:** Test fix was necessary for correctness with random data generation. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Regime trend strategy now has adaptive thresholds and trail metadata
- Trail metadata is ready for future execution layer integration
- All 179 signals tests pass (no regressions)

---
*Phase: 03-liquidation-correlation-regime*
*Completed: 2026-03-22*
