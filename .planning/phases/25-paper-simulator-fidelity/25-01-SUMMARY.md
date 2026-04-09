---
phase: 25-paper-simulator-fidelity
plan: 01
subsystem: reconciliation/paper_simulator
tags: [paper-trading, fill-model, adverse-selection, tdd]
dependency_graph:
  requires: []
  provides: [probabilistic-fill-model, adverse-selection, yaml-paper-config]
  affects: [agents/reconciliation/paper_simulator.py, agents/reconciliation/main.py, configs/default.yaml]
tech_stack:
  added: []
  patterns: [frozen-dataclass-validation, probabilistic-fill, adverse-selection-bps, proximity-factor]
key_files:
  created: []
  modified:
    - agents/reconciliation/paper_simulator.py
    - agents/reconciliation/tests/test_paper_simulator.py
    - agents/reconciliation/main.py
    - configs/default.yaml
decisions:
  - PaperSimulatorConfig defaults (fill_probability_base=0.7, adverse_selection_bps=5, sl_slippage_bps=10) match realistic liquidity; backward compat requires base=1.0, bps=0
  - proximity_factor = 1 / (1 + distance_bps/100) chosen for smooth decay without hyperparameters
  - MARKET orders bypass adverse selection entirely — mark price returned directly
  - rng = random.Random() (auto-seeded) in production; tests use seeded Random(N) for determinism
metrics:
  duration_minutes: 8
  completed_date: "2026-04-09T00:31:40Z"
  tasks_completed: 2
  files_modified: 4
---

# Phase 25 Plan 01: Paper Simulator Probabilistic Fill Model Summary

**One-liner:** Proximity-scaled fill probability with basis-point adverse selection wired into paper simulator order_processor, loaded from YAML config.

## What Was Built

Added a probabilistic fill model to the paper trading simulator that makes paper P&L match real-world execution more closely:

- **`PaperSimulatorConfig`** — frozen dataclass with three fields: `fill_probability_base` (range [0,1]), `adverse_selection_bps` (range [0,50]), `sl_slippage_bps` (range [0,50]). `__post_init__` validates all ranges and raises `ValueError` with descriptive messages.
- **`_decide_fill()`** — module-level function returning `(bool, Decimal)`. MARKET orders always fill at mark price. LIMIT orders use proximity-scaled fill probability: `probability = fill_probability_base × 1/(1 + distance_bps/100)`. Filled limit orders apply adverse selection (BUY pays more, SELL receives less). Division-by-zero guard for mark_price=0.
- **`order_processor` integration** — replaced the direct fill_price block with a `_decide_fill()` call. Unfilled orders are logged at DEBUG level as `paper_fill_skipped` and acknowledged without applying a fill.
- **YAML config** — `paper_simulator:` section added to `configs/default.yaml` with production defaults.
- **`main.py` wiring** — loads config from YAML into `PaperSimulatorConfig` and passes `cfg=sim_cfg` to `run_paper_simulator`. Falls back to defaults on file not found or YAML parse error.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | TDD — PaperSimulatorConfig and _decide_fill with tests | 9fc3476 | paper_simulator.py, test_paper_simulator.py |
| 2 | Wire _decide_fill into order_processor and add YAML config | 0ad935f | paper_simulator.py, main.py, default.yaml |

## Test Coverage

10 new tests added across 3 classes:
- `TestFillDecision` (5 tests): MARKET always fills, LIMIT at-mark with base 1.0/0.0, far-from-mark has lower fill rate, zero mark price guard, backward compat
- `TestAdverseSelection` (4 tests): BUY raises price, SELL lowers price, MARKET no adverse selection, zero bps no change
- `TestConfigValidation` (1 test): rejects fill_probability_base > 1, adverse_selection_bps < 0, sl_slippage_bps > 50

All 49 tests pass (39 existing + 10 new). No regressions.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — `sl_slippage_bps` is stored in config and validated but not yet applied to stop-loss fill prices (plan scope was entry orders only). This is intentional per plan scope; stop-loss slippage wiring is a separate concern.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced. Config loaded via `yaml.safe_load` (not `yaml.load`). `__post_init__` validation (T-25-01, T-25-02) implemented. mark_price=0 guard (T-25-03) implemented.

## Self-Check: PASSED

- `agents/reconciliation/paper_simulator.py` — FOUND, contains `PaperSimulatorConfig`, `_decide_fill`, `paper_fill_skipped`
- `agents/reconciliation/tests/test_paper_simulator.py` — FOUND, contains `TestFillDecision`, `TestAdverseSelection`, `TestConfigValidation`
- `configs/default.yaml` — FOUND, contains `paper_simulator:` section
- `agents/reconciliation/main.py` — FOUND, contains `PaperSimulatorConfig`, `cfg=sim_cfg`
- Commit 9fc3476 — FOUND
- Commit 0ad935f — FOUND
