---
phase: 02-momentum-mean-reversion
plan: 01
subsystem: signals
tags: [momentum, ema-crossover, volume-filter, swing-stops, portfolio-routing, scipy, atr-percentile]

requires:
  - phase: 01-foundation-tuning
    provides: "Per-instrument configs, strategy matrix, FeatureStore bar_volumes, SignalSource enums"
provides:
  - "Momentum strategy with volume confirmation filter (MOM-01)"
  - "Adaptive conviction model using ADX + RSI + volatility percentile (MOM-02)"
  - "Swing point stop-loss placement with ATR fallback (MOM-03)"
  - "Portfolio A routing for high-conviction momentum signals (MOM-04)"
  - "Fixed YAML loader for all 17 MomentumParams fields (D-07)"
  - "Momentum enabled with weight 0.20 for all 5 instruments (D-05, D-06)"
affects: [alpha-combiner, risk-agent, execution-agent]

tech-stack:
  added: []
  patterns:
    - "Three-component conviction model (ADX 0-0.35, RSI 0-0.35, vol/volatility 0-0.30)"
    - "Swing point detection for structure-aware stop placement"
    - "scipy.stats.percentileofscore for ATR volatility percentile"
    - "Portfolio A routing via configurable conviction threshold"

key-files:
  created: []
  modified:
    - agents/signals/strategies/momentum.py
    - configs/strategies/momentum.yaml
    - configs/strategy_matrix.yaml
    - agents/signals/tests/test_momentum.py

key-decisions:
  - "Used scipy.stats.percentileofscore for ATR percentile (already a project dependency)"
  - "Swing stop uses swing point only when below entry (LONG) or above entry (SHORT); ATR fallback otherwise"
  - "Portfolio A threshold set to 0.75 conviction (higher than mean reversion's 0.65)"
  - "Volume filter uses np.abs on bar_volumes since values can be negative"

patterns-established:
  - "Three-component conviction model: ADX, RSI, and volume/volatility components capped independently then summed"
  - "Swing point detection with configurable lookback and order parameters"
  - "Volume confirmation as a pre-conviction gate (reject before computing conviction)"

requirements-completed: [MOM-01, MOM-02, MOM-03, MOM-04]

duration: 4min
completed: 2026-03-22
---

# Phase 02 Plan 01: Momentum Strategy Improvements Summary

**Momentum strategy enhanced with volume confirmation, 3-component adaptive conviction (ADX+RSI+volatility), swing-point stop-loss placement, and Portfolio A routing at conviction >= 0.75**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T09:27:22Z
- **Completed:** 2026-03-22T09:31:18Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 4

## Accomplishments
- Volume confirmation filter rejects EMA crossovers when bar volume < 50% of rolling average (MOM-01)
- Redesigned conviction model with 3 components: ADX (0-0.35), RSI (0-0.35), volume/volatility (0-0.30) using ATR percentile (MOM-02)
- Swing point stop-loss: LONG stops at recent swing low, SHORT stops at swing high, with ATR fallback (MOM-03)
- High-conviction signals (>= 0.75) route to Portfolio A for autonomous execution (MOM-04)
- Fixed YAML loader to load all 17 params (was missing 5: adx_threshold, adx_period, cooldown_bars, stop_loss_atr_mult, take_profit_atr_mult) (D-07)
- Momentum enabled with weight 0.20 for all 5 instruments in strategy matrix (D-05, D-06)
- 21 tests passing across 7 test classes

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for all momentum improvements** - `1fea9bc` (test)
2. **Task 1 GREEN: Volume filter, adaptive conviction, swing stops, Portfolio A routing** - `ac39f21` (feat)

**Plan metadata:** [pending] (docs: complete plan)

## Files Created/Modified
- `agents/signals/strategies/momentum.py` - Added volume filter, 3-component conviction, swing stops, Portfolio A routing, fixed YAML loader
- `configs/strategies/momentum.yaml` - Enabled with weight 0.20, all 17 params configured
- `configs/strategy_matrix.yaml` - Momentum enabled for ETH/BTC/SOL/QQQ/SPY
- `agents/signals/tests/test_momentum.py` - 21 tests: config, volume filter, adaptive conviction, swing stops, portfolio routing

## Decisions Made
- Used scipy.stats.percentileofscore for ATR volatility percentile (already a project dependency, no new deps)
- Swing stop uses the swing point only when it makes sense as a stop (below entry for LONG, above for SHORT); otherwise falls back to ATR-based stops
- Portfolio A conviction threshold of 0.75 is higher than mean reversion's 0.65, reflecting the plan's requirement for momentum to have stricter routing
- Volume filter applied as a gate before conviction computation -- rejected crossovers never compute conviction at all, saving compute

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Momentum strategy fully operational with all 4 improvements
- Ready for Phase 02 Plan 02 (mean reversion improvements)
- Alpha combiner will receive momentum signals with new metadata fields (volume_ratio, vol_percentile, swing_stop)

---
*Phase: 02-momentum-mean-reversion*
*Completed: 2026-03-22*
