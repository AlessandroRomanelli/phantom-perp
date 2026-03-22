---
phase: 04-new-strategies
plan: 02
subsystem: signals
tags: [orderbook-imbalance, obi, time-weighted-average, depth-gate, portfolio-routing]

# Dependency graph
requires:
  - phase: 01-foundation-tuning
    provides: SignalStrategy base class, FeatureStore with orderbook_imbalances, strategy matrix
provides:
  - OrderbookImbalanceStrategy with time-weighted imbalance analysis
  - Per-instrument OBI config with depth gate tuning
  - Strategy matrix enablement for all 5 instruments
affects: [05-cross-cutting-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: [time-weighted-average-via-linear-weights, depth-gate-spread-filter, 3-component-conviction]

key-files:
  created:
    - agents/signals/strategies/orderbook_imbalance.py
    - agents/signals/tests/test_orderbook_imbalance.py
  modified:
    - agents/signals/main.py
    - configs/strategies/orderbook_imbalance.yaml
    - configs/strategy_matrix.yaml

key-decisions:
  - "3-component conviction: imbalance magnitude (0-0.45) + spread quality (0-0.30) + volume ratio (0-0.25)"
  - "Time-weighted average uses linear weights [1,2,...,N] over lookback window for recency bias"
  - "Depth gate suppresses signals when spread_bps > max_spread_bps (proxy for thin orderbook)"
  - "Portfolio A routing at conviction >= 0.65 for autonomous execution"

patterns-established:
  - "Time-weighted averaging pattern: np.average(window, weights=np.arange(1, N+1))"
  - "Depth gate pattern: spread_bps threshold as orderbook quality proxy"

requirements-completed: [OBI-01, OBI-02, OBI-03, OBI-04]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 04 Plan 02: Orderbook Imbalance Strategy Summary

**OBI strategy with time-weighted bid/ask imbalance, spread-based depth gate, and 3-component conviction model for short-horizon directional signals**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T10:45:04Z
- **Completed:** 2026-03-22T10:48:53Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- OrderbookImbalanceStrategy implementing SignalStrategy ABC with time-weighted imbalance analysis
- Depth gate suppresses signals on thin orderbooks (spread > max_spread_bps)
- 3-component conviction model: imbalance magnitude + spread quality + volume ratio
- Per-instrument configs for ETH, BTC, SOL, QQQ, SPY with tuned thresholds
- 14 test cases covering all OBI requirements including edge cases

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Add failing tests** - `4bff955` (test)
2. **Task 1 (GREEN): Implement OBI strategy** - `549387e` (feat)
3. **Task 2: Register and configure** - `fafb966` (feat)

_TDD task had RED + GREEN commits._

## Files Created/Modified
- `agents/signals/strategies/orderbook_imbalance.py` - OBI strategy with TWA, depth gate, conviction model
- `agents/signals/tests/test_orderbook_imbalance.py` - 14 test cases for all OBI behaviors
- `agents/signals/main.py` - Strategy registration in STRATEGY_CLASSES and STRATEGY_PARAMS_CLASSES
- `configs/strategies/orderbook_imbalance.yaml` - Full config with per-instrument overrides
- `configs/strategy_matrix.yaml` - OBI enabled for all 5 instruments

## Decisions Made
- 3-component conviction model with imbalance (0-0.45), spread (0-0.30), volume (0-0.25) to avoid single-factor noise
- Linear weights for time-weighted average -- simple, interpretable, gives 2x weight to most recent vs midpoint
- Per-instrument spread gates: BTC 10bps (deepest book), ETH 15bps, SOL 25bps, QQQ/SPY 30bps (equity perps)
- Higher min_conviction for SOL/QQQ/SPY (0.50 vs default 0.45) due to noisier orderbook data
- Division-by-zero guard when imbalance_threshold is 0 (edge case in conviction calculation)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed division by zero in conviction model**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** When imbalance_threshold=0.0 (used in test_time_weighted_average), conviction calculation divided by zero
- **Fix:** Added guard: if threshold > 0 use ratio scaling, else use absolute imbalance scaling
- **Files modified:** agents/signals/strategies/orderbook_imbalance.py
- **Verification:** All 14 tests pass including test_time_weighted_average
- **Committed in:** 549387e (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary for correctness with edge-case parameter values. No scope creep.

## Issues Encountered
- Parallel agent modified shared files (libs/common/constants.py, agents/signals/main.py) causing import errors in some test files -- out of scope, does not affect OBI tests or functionality

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- OBI strategy complete and registered, ready for cross-cutting quality phase
- All 5 instruments enabled with per-instrument parameter tuning
- Signal interface follows established patterns (compatible with alpha combiner)

---
*Phase: 04-new-strategies*
*Completed: 2026-03-22*
