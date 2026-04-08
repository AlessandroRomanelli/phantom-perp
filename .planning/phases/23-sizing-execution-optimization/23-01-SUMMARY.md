---
phase: 23-sizing-execution-optimization
plan: 01
subsystem: risk, execution, config
tags: [sizing, stop-loss, conviction, fees, orderbook-imbalance]
dependency_graph:
  requires: []
  provides: [PROF-01, PROF-02, ROBU-06]
  affects: [agents/risk/position_sizer.py, agents/execution/stop_loss_manager.py, agents/execution/config.py]
tech_stack:
  added: []
  patterns: [TDD red-green, convex conviction scaling, STOP_LIMIT maker orders]
key_files:
  created: []
  modified:
    - configs/default.yaml
    - configs/strategies/orderbook_imbalance.yaml
    - agents/execution/stop_loss_manager.py
    - agents/execution/config.py
    - agents/execution/main.py
    - agents/execution/order_placer.py
    - agents/execution/tests/test_stop_loss_manager.py
    - agents/execution/tests/test_config.py
    - agents/risk/tests/test_computations.py
decisions:
  - conviction_power set to 1.0 (linear) — forensic audit showed 2.0 (quadratic) shrinks mid-conviction trades to 25% of max, insufficient to cover fees
  - STOP_LIMIT with 10 bps limit buffer — pays maker fee on SL trigger instead of taker; configurable to widen on gapped markets
  - BTC OBI cooldown doubled to 20 bars — reduces fee-negative high-frequency signals on tighter-spread BTC
metrics:
  duration: 15m
  completed: "2026-04-08T23:45:48Z"
  tasks_completed: 2
  files_changed: 9
---

# Phase 23 Plan 01: Sizing & Execution Config Summary

**One-liner:** Linear conviction scaling (power=1.0), STOP_LIMIT maker stop-loss with 10 bps buffer, and BTC OBI cooldown doubled to 20 bars to improve per-trade profitability.

## What Was Built

### Task 1: YAML Config Changes (PROF-01, ROBU-06)

Changed `conviction_power` from `2.0` to `1.0` in `configs/default.yaml`. At the old setting, a 0.5-conviction signal produced 25% of max position size — too small to generate enough notional to recover round-trip fees. At 1.0 (linear), it produces 50% of max.

Added `cooldown_bars: 20` under `BTC-PERP` in `configs/strategies/orderbook_imbalance.yaml`, doubling the default of 10. This reduces OBI signal frequency on BTC where the tight spread makes consecutive fee-negative trades likely.

Test additions to `TestConvexSizing`:
- `test_default_limits_use_linear_conviction` — asserts `_default_limits()` (no override) gives conviction=0.5 → 50% of max (was 25%)
- `test_half_conviction_absolute_size_with_linear_power` — verifies absolute size ≈ 1.5 ETH at power=1.0, conviction=0.5
- `test_maker_fee_round_trip_under_one_pct` — documents that round-trip maker fee (0.025%) is well under 1% of notional

### Task 2: STOP_LIMIT Stop-Loss with Configurable Buffer (PROF-02)

Modified `build_protective_orders()` in `stop_loss_manager.py`:
- Added `sl_limit_buffer_bps: int = 10` parameter
- Stop-loss orders now use `OrderType.STOP_LIMIT` instead of `STOP_MARKET`
- LONG: `limit_price = round(stop_price × (1 − buffer))` — below trigger
- SHORT: `limit_price = round(stop_price × (1 + buffer))` — above trigger
- `sl_limit_buffer_bps=0` produces `limit_price == stop_price`

Added `sl_limit_buffer_bps: int = 10` field to `ExecutionConfig` with `load_execution_config` parsing. Added `sl_limit_buffer_bps: 10` to `configs/default.yaml` under `execution:`.

Updated both call sites:
- `agents/execution/main.py` `_place_protective_orders()`: added `sl_limit_buffer_bps` param, call site passes `exec_config.sl_limit_buffer_bps`
- `agents/execution/order_placer.py` `build_result_from_response()`: added `sl_limit_buffer_bps: int = 10` param, passes through to `build_protective_orders`

Test additions to `TestStopLimitBuffer` (new class):
- `test_long_sl_limit_price_below_stop` — limit_price < stop_price for LONG
- `test_short_sl_limit_price_above_stop` — limit_price > stop_price for SHORT
- `test_zero_buffer_limit_equals_stop` — sl_limit_buffer_bps=0 → limit_price == stop_price
- `test_buffer_10_bps_calculation` — 2100 × 0.999 = 2097.90 exact value

Config tests added:
- `test_sl_limit_buffer_bps_default` — defaults to 10 when absent from YAML
- `test_sl_limit_buffer_bps_parsed` — parses custom value (15) from YAML

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 37e9dcc | PROF-01/ROBU-06 — linear conviction sizing and BTC OBI cooldown |
| 2 | ffc181f | PROF-02 — STOP_LIMIT stop-loss with configurable limit buffer |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced.

## Self-Check: PASSED

- All 9 modified files exist on disk
- conviction_power: 1.0 in configs/default.yaml ✓
- cooldown_bars: 20 in configs/strategies/orderbook_imbalance.yaml ✓
- STOP_LIMIT in agents/execution/stop_loss_manager.py ✓
- sl_limit_buffer_bps in agents/execution/config.py ✓
- sl_limit_buffer_bps: 10 in configs/default.yaml ✓
- Commit 37e9dcc exists ✓
- Commit ffc181f exists ✓
- 148 passed, 5 skipped (0 failures) across risk + execution test suites ✓
