---
phase: 23-sizing-execution-optimization
plan: "02"
subsystem: risk
tags: [fee-filter, profitability, risk-engine, PROF-05]
dependency_graph:
  requires: [23-01]
  provides: [fee-adjusted-edge-filter]
  affects: [agents/risk/main.py, agents/risk/limits.py, configs/default.yaml]
tech_stack:
  added: []
  patterns: [fee-drag-filter, conviction-scaling]
key_files:
  created: []
  modified:
    - agents/risk/main.py
    - agents/risk/limits.py
    - agents/risk/tests/test_risk_engine.py
    - agents/risk/tests/test_computations.py
    - configs/default.yaml
decisions:
  - "Fee filter uses quantized estimate_fee() result — zero fee rounds to zero round_trip_fee, which never rejects (by design for micro-trades)"
  - "Rejection threshold: conviction < 2*FEE_MAKER/min_expected_move_pct = 0.05 for default config"
  - "Updated LIMITS_A/LIMITS_B test fixtures to conviction_power=1.0 to match plan 01 config change — required for fee filter tests to produce meaningful notionals"
metrics:
  duration_seconds: 135
  completed_date: "2026-04-08"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 5
---

# Phase 23 Plan 02: Fee-Adjusted Edge Filter Summary

**One-liner:** Fee drag filter in RiskEngine step 14 — rejects trades where round-trip maker fees exceed conviction-weighted expected edge (threshold: conviction < 0.05 at default 0.5% min-move config).

## What Was Built

Added a fee-adjusted signal filter (step 14) to `RiskEngine.evaluate()` that rejects trades where the estimated round-trip trading fee exceeds the expected gross profit from the trade's conviction and minimum expected move.

**Filter math:**
- `round_trip_fee = fee * 2` (entry + exit, both estimated as maker at 0.0125%)
- `expected_gross = notional * conviction * min_expected_move_pct`
- Reject when `round_trip_fee > expected_gross`
- Breakeven conviction at default config: `conviction = 2 * 0.000125 / 0.005 = 0.05`

This directly addresses the forensic finding that the system was paying $4 in fees for every $1 of alpha captured.

## Tasks

| # | Name | Commit | Files |
|---|------|--------|-------|
| RED | TestFeeEdgeFilter failing tests | da5df34 | test_risk_engine.py |
| GREEN | Fee filter implementation | 71c0a8a | main.py, limits.py, test_risk_engine.py, test_computations.py, default.yaml |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Updated conviction_power in test fixtures to 1.0**
- **Found during:** RED phase test design
- **Issue:** With conviction_power=2.0 (previous default in test fixtures), very low conviction values produce micro-trades with fee quantized to $0.00, making the fee filter mathematically inactive for any conviction below threshold. The filter was designed for linear conviction scaling.
- **Fix:** Added `conviction_power=1.0` to LIMITS_A and LIMITS_B in test_risk_engine.py. This matches the plan 01 config change that set `conviction_power: 1.0` in default.yaml.
- **Files modified:** agents/risk/tests/test_risk_engine.py
- **Commit:** da5df34

## Known Stubs

None — fee filter is fully wired. `min_expected_move_pct` is read from config and applied in every `evaluate()` call.

## Threat Flags

None — no new network endpoints or trust boundaries introduced.

## Self-Check: PASSED

- [x] `agents/risk/main.py` modified with "Fee drag" filter at step 14
- [x] `agents/risk/limits.py` has `min_expected_move_pct` field
- [x] `configs/default.yaml` has `min_expected_move_pct: 0.005` under `risk.global`
- [x] `agents/risk/tests/test_risk_engine.py` has `TestFeeEdgeFilter` class
- [x] Commits da5df34 and 71c0a8a exist
- [x] 98 risk tests passing (0 failures)
