---
phase: 25-paper-simulator-fidelity
plan: 02
subsystem: reconciliation/paper_simulator
tags: [tdd, paper-simulator, stop-loss, slippage, fidelity]
dependency_graph:
  requires: [25-01]
  provides: [sl-slippage-in-paper-mode]
  affects: [reconciliation]
tech_stack:
  added: []
  patterns: [TDD red-green, pure function extraction, closure capture]
key_files:
  created: []
  modified:
    - agents/reconciliation/paper_simulator.py
    - agents/reconciliation/tests/test_paper_simulator.py
decisions:
  - _apply_sl_slippage extracted as standalone function for testability — slippage applied inline in protective_order_monitor via closure capture of sim_cfg
  - slippage direction always adverse: BUY fills higher, SELL fills lower
  - sl_slippage_bps=0 short-circuits early, identical behavior to pre-phase code
metrics:
  duration: ~5 minutes
  completed: 2026-04-09T00:34:45Z
  tasks_completed: 2
  files_modified: 2
---

# Phase 25 Plan 02: Stop-Loss Slippage Summary

## One-liner

Stop-loss orders in paper mode now apply configurable adverse slippage (default 10 bps) via `_apply_sl_slippage()` wired into `protective_order_monitor`.

## What Was Built

Added realistic stop-loss slippage to the paper simulator's protective order monitor. Real stop-loss fills degrade in fast markets; the paper simulator previously filled at the exact trigger price, making paper P&L overly optimistic on losing trades.

### `_apply_sl_slippage()` (paper_simulator.py)

Pure function placed after `_decide_fill()`. Handles three cases:
1. `is_stop_loss=False` → return `fill_price` unchanged (TP, no slippage)
2. `sl_slippage_bps == 0` → return `fill_price` unchanged (zero config)
3. SL BUY-to-close → `fill_price * (1 + bps/10000)` quantized to 2dp
4. SL SELL-to-close → `fill_price * (1 - bps/10000)` quantized to 2dp

### `protective_order_monitor` wiring

Before each `apply_fill()` call in the triggered-order loop, computes:
```python
effective_fill_price = _apply_sl_slippage(
    fill_price=order.fill_price,
    side=order.side,
    is_stop_loss=order.is_stop_loss,
    cfg=sim_cfg,
)
```
`sim_cfg` is captured from the enclosing `run_paper_simulator` scope (set in Plan 01).

Log line extended with `slippage_bps` field for observability.

## Tests Added

`TestSLSlippage` class (5 tests) in `test_paper_simulator.py`:
- `test_sl_buy_to_close_slippage_raises_price`: BUY SL, 10 bps → 2000.00 → 2002.00
- `test_sl_sell_to_close_slippage_lowers_price`: SELL SL, 10 bps → 2000.00 → 1998.00
- `test_tp_no_slippage`: is_stop_loss=False → price unchanged
- `test_zero_slippage_bps_no_change`: sl_slippage_bps=0 → price unchanged
- `test_slippage_quantized`: result has at most 2 decimal places

## Deviations from Plan

None — plan executed exactly as written.

## Verification

```
python3 -m pytest agents/reconciliation/tests/test_paper_simulator.py::TestSLSlippage -x -q
5 passed

python3 -m pytest agents/reconciliation/tests/ -q
108 passed
```

## Known Stubs

None.

## Threat Flags

None — no new trust boundaries introduced. `sl_slippage_bps` bounds validated in `PaperSimulatorConfig.__post_init__` (Plan 01, T-25-04). Direction correctness verified by unit tests (T-25-05).

## Self-Check

### Files exist:
- [x] `agents/reconciliation/paper_simulator.py` — contains `_apply_sl_slippage` and `effective_fill_price`
- [x] `agents/reconciliation/tests/test_paper_simulator.py` — contains `TestSLSlippage`

### Commits exist:
- [x] `1fbe492` — test(25-02): add failing TestSLSlippage tests
- [x] `cad3702` — feat(25-02): add _apply_sl_slippage and wire into protective_order_monitor

## Self-Check: PASSED
