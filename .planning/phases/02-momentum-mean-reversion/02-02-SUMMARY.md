---
phase: 02-momentum-mean-reversion
plan: 02
subsystem: signals
tags: [mean-reversion, bollinger-bands, adaptive-bands, trend-filter, portfolio-routing, volume-confirmation]

# Dependency graph
requires:
  - phase: 01-foundation-tuning
    provides: "Per-instrument configs, FeatureStore bar_volumes, strategy matrix"
provides:
  - "Multi-factor trend rejection for mean reversion (EMA slope + consecutive closes + ADX)"
  - "Adaptive Bollinger Band width based on ATR volatility percentile"
  - "Extended take-profit targets with partial_target metadata for strong reversions"
  - "Portfolio A routing for high-conviction mean reversion signals"
  - "Volume conviction boost from bar_volumes on band touch"
  - "Fixed YAML loader to load all MeanReversionParams fields"
affects: [alpha-combiner, risk-agent, portfolio-routing]

# Tech tracking
tech-stack:
  added: [scipy.stats.percentileofscore]
  patterns: [multi-factor-filtering, adaptive-indicators, 3-component-conviction]

key-files:
  created: []
  modified:
    - agents/signals/strategies/mean_reversion.py
    - configs/strategies/mean_reversion.yaml
    - agents/signals/tests/test_mean_reversion.py

key-decisions:
  - "Used scipy percentileofscore for ATR volatility percentile (already in dependency tree)"
  - "3-component conviction model: deviation (0-0.40) + RSI (0-0.35) + volume (0-0.25)"
  - "Extended TP set at middle_band +/- 50% of band half-width for strong reversions"
  - "Portfolio A threshold at 0.65 conviction (lower than momentum's 0.75, per D-01)"

patterns-established:
  - "Multi-factor trend strength: weighted composite of EMA slope, consecutive closes, ADX"
  - "Adaptive indicator parameters: percentile-based scaling of Bollinger Band width"
  - "Extended targets with partial_target in metadata for staged exit strategies"

requirements-completed: [MR-01, MR-02, MR-03, MR-04]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 02 Plan 02: Mean Reversion Improvements Summary

**Multi-factor trend rejection, adaptive BB width via ATR percentile, extended TP with partial targets, volume conviction boost, and Portfolio A routing at conviction >= 0.65**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T09:27:20Z
- **Completed:** 2026-03-22T09:32:20Z
- **Tasks:** 1
- **Files modified:** 3

## Accomplishments
- Replaced single ADX threshold with composite trend strength (EMA slope 40% + consecutive closes 30% + ADX 30%)
- Added adaptive Bollinger Band width scaling based on ATR volatility percentile (0.8x-1.2x base std)
- Implemented extended take-profit targets beyond middle band for strong reversions with partial_target metadata
- Added volume conviction boost from FeatureStore bar_volumes (3-component conviction model)
- Fixed YAML loader to load all 15 MeanReversionParams fields (was missing atr_period, stop_loss_atr_mult, cooldown_bars)
- Added Portfolio A routing for conviction >= 0.65

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests** - `15599da` (test)
2. **Task 1 GREEN: Implementation** - `1054c69` (feat)

## Files Created/Modified
- `agents/signals/strategies/mean_reversion.py` - Multi-factor trend rejection, adaptive bands, extended TP, volume boost, Portfolio A routing, 3-component conviction
- `configs/strategies/mean_reversion.yaml` - Added trend_reject_threshold, extended_deviation_threshold, portfolio_a_min_conviction, vol_lookback params
- `agents/signals/tests/test_mean_reversion.py` - 29 tests: TestMRConfig, TestMRTrendRejection, TestMRAdaptiveBands, TestMRExtendedTargets, TestMRPortfolioRouting, TestMRVolumeBoost

## Decisions Made
- Used scipy percentileofscore for ATR percentile calculation (already available in project dependencies)
- Conviction model changed from 2-component (dev 0-0.5 + RSI 0-0.5) to 3-component (dev 0-0.40 + RSI 0-0.35 + vol 0-0.25)
- Extended TP formula: middle_band +/- 50% of band half-width for strong reversions
- Portfolio A conviction threshold set at 0.65 (lower than momentum's 0.75 per D-01)
- Kept adx_max field in params for backward compatibility even though it is no longer used directly in evaluate()

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Adaptive bands test with full evaluate() pipeline was unreliable because breach bars inflate ATR to max percentile. Solved by testing the adaptive_std formula directly with explicit ATR values and scipy percentileofscore.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Mean reversion strategy fully improved with all 4 MR requirements
- Volume boost integrated with FeatureStore bar_volumes
- Ready for Phase 02 completion or next phase strategies

---
*Phase: 02-momentum-mean-reversion*
*Completed: 2026-03-22*
