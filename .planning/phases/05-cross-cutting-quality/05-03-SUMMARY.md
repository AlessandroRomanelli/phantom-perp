---
phase: 05-cross-cutting-quality
plan: 03
subsystem: config
tags: [yaml, per-instrument, tuning, momentum, mean-reversion, correlation]

requires:
  - phase: 01-foundation-tuning
    provides: "Per-instrument YAML config pattern and Phase 1 instrument overrides"
  - phase: 02-momentum-mean-reversion
    provides: "Momentum volume confirmation, swing stops, adaptive conviction; MR trend rejection, adaptive bands"
  - phase: 03-liquidation-correlation-regime
    provides: "Correlation multi-window basis, funding integration"
  - phase: 04-new-strategies-funding
    provides: "Funding rate boost integration across strategies"
  - phase: 05-cross-cutting-quality
    provides: "05-01 unified Portfolio A routing threshold"
provides:
  - "Per-instrument tuning for momentum Phase 2+4 params (vol confirmation, swing stops, funding boost) across 5 instruments"
  - "Per-instrument tuning for mean reversion Phase 2+4 params (trend rejection, extended targets, funding boost) across 5 instruments"
  - "Per-instrument tuning for correlation Phase 3+4 params (multi-window lookbacks, funding integration) across 5 instruments"
affects: []

tech-stack:
  added: []
  patterns:
    - "Asset-characteristic-driven tuning: SOL widest/fastest, BTC longest/strictest, ETH mid, QQQ/SPY conservative"

key-files:
  created: []
  modified:
    - configs/strategies/momentum.yaml
    - configs/strategies/mean_reversion.yaml
    - configs/strategies/correlation.yaml

key-decisions:
  - "Momentum instruments section added fresh (none existed); MR and correlation updated existing sections"
  - "Correlation cooldown_bars not tuned per-instrument because __init__ does not load it from config"
  - "SOL gets most permissive trend rejection (0.55) and highest funding boost (0.10-0.12) across all strategies"
  - "BTC gets strictest trend rejection (0.7) and longest lookback windows across all strategies"
  - "Mean reversion cooldown_bars updated: BTC 8->12, QQQ 10->12, SPY 10->15 for longer equity/BTC cycles"

patterns-established:
  - "Per-instrument tuning covers all strategy params added in Phases 2-4, not just Phase 1 originals"

requirements-completed: [XQ-01, XQ-05]

duration: 3min
completed: 2026-03-22
---

# Phase 05 Plan 03: Per-Instrument Tuning Refresh Summary

**Per-instrument YAML overrides for momentum, mean reversion, and correlation strategies covering all Phase 2-4 params with asset-characteristic-derived values**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-22T12:30:04Z
- **Completed:** 2026-03-22T12:32:18Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added instruments section to momentum.yaml with 5 instruments covering vol_lookback, vol_min_ratio, swing_lookback, swing_order, funding_rate_boost, adx_threshold
- Updated mean_reversion.yaml with trend_reject_threshold, extended_deviation_threshold, funding_rate_boost per instrument
- Updated correlation.yaml with basis_short/medium/long_lookback, funding_z_score_threshold, funding_rate_boost per instrument
- All 261 existing tests pass after changes

## Task Commits

Each task was committed atomically:

1. **Task 1: Per-instrument momentum tuning for Phase 2+4 params** - `48e4a18` (feat)
2. **Task 2: Per-instrument tuning for mean reversion and correlation** - `a8624a1` (feat)

## Files Created/Modified
- `configs/strategies/momentum.yaml` - Added instruments section with 5 per-instrument overrides for Phase 2+4 params
- `configs/strategies/mean_reversion.yaml` - Added Phase 2+4 params (trend_reject_threshold, extended_deviation_threshold, funding_rate_boost) to existing instrument sections
- `configs/strategies/correlation.yaml` - Added Phase 3+4 params (multi-window lookbacks, funding z-score threshold, funding_rate_boost) to existing instrument sections

## Decisions Made
- Correlation strategy's `__init__` does not load `cooldown_bars`, `atr_period`, `stop_loss_atr_mult`, or `take_profit_atr_mult` from config, so those were not added to per-instrument overrides (only params actually consumed by the strategy code were tuned)
- Mean reversion cooldown_bars adjusted upward for BTC (8->12), QQQ (10->12), SPY (10->15) to reflect longer cycles
- SOL mean reversion min_conviction lowered to 0.25 (most permissive) and stop_loss_atr_mult widened to 2.5

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 3 strategies now have complete per-instrument tuning covering Phases 1-4 params
- Phase 05 cross-cutting quality work complete

---
*Phase: 05-cross-cutting-quality*
*Completed: 2026-03-22*
