---
phase: 11-metrics-engine
plan: "02"
subsystem: metrics
tags: [tdd, metrics, expectancy, profit-factor, drawdown, fee-adjustment, min-count-gate]
dependency_graph:
  requires: [libs/metrics/engine.py (RoundTrip, build_round_trips from Plan 01)]
  provides: [libs/metrics/engine.py (StrategyMetrics, compute_strategy_metrics)]
  affects: [Phase 13 (Claude integration consumes StrategyMetrics), Phase 14 (tuner agent)]
tech_stack:
  added: []
  patterns: [frozen dataclass with slots=True, FIFO min-count gate, O(n) drawdown sweep, Decimal(str(float)) for rate conversion]
key_files:
  created: []
  modified:
    - libs/metrics/engine.py
    - libs/metrics/__init__.py
    - tests/unit/test_metrics_engine.py
decisions:
  - "Zero-P&L round-trips classified as losses (conservative per net_pnl <= 0 -- fees make true breakeven negative)"
  - "funding_costs_usdc = Decimal(0) placeholder per D-08 (position lifecycle not available in Phase 10)"
  - "total_net_pnl = total_gross_pnl - total_fees_usdc - funding_costs_usdc for METR-04 interface contract"
  - "Pairs below min_trades=10 return None in result dict (clean contract for Phase 13 per D-01/D-02)"
  - "Still-in-drawdown check uses datetime.now(timezone.utc) per D-07"
metrics:
  duration_minutes: 8
  completed_date: "2026-03-25"
  tasks_completed: 2
  files_changed: 3
---

# Phase 11 Plan 02: Metrics Computation Layer Summary

## One-liner

StrategyMetrics frozen dataclass and compute_strategy_metrics() function implementing expectancy (METR-01), profit factor (METR-02), drawdown amount+duration (METR-03), fee-adjusted P&L with funding placeholder (METR-04/D-08), and min-count gate (D-01/D-02), with 16 new TDD tests (30 total passing).

## What Was Built

### libs/metrics/engine.py (additions)

**StrategyMetrics** frozen dataclass with 16 fields:
- Identity: `primary_source`, `instrument`
- Counts: `trade_count`, `win_count`, `loss_count`
- Rates: `win_rate` (float)
- Expectancy (METR-01): `avg_win_usdc`, `avg_loss_usdc`, `expectancy_usdc` (all Decimal)
- Profit factor (METR-02): `profit_factor` (float | None -- None when gross_loss == 0)
- Fee-adjusted P&L (METR-04/D-09): `total_gross_pnl`, `total_fees_usdc`, `funding_costs_usdc`, `total_net_pnl` (all Decimal)
- Drawdown (METR-03): `max_drawdown_usdc` (Decimal), `max_drawdown_duration_hours` (float)

**_compute_metrics(source, instrument, round_trips)** — Internal helper. Win/loss classification on net_pnl basis (zero-pnl = loss). Expectancy via `avg_win * Decimal(str(win_rate)) - avg_loss * Decimal(str(loss_rate))`. Profit factor via gross sums with None guard. O(n) drawdown sweep with still-in-drawdown check using `datetime.now(timezone.utc)`. Fee aggregates with `funding_costs_usdc = Decimal("0")` placeholder.

**compute_strategy_metrics(fills, min_trades=10)** — Public function. Calls `build_round_trips()` then applies min-count gate per (primary_source, instrument) key. Returns None for pairs with fewer than `min_trades` closed round-trips; returns StrategyMetrics otherwise. Handles empty fills input by returning empty dict.

### libs/metrics/__init__.py (additions)

Added `StrategyMetrics` and `compute_strategy_metrics` to imports and `__all__`.

### tests/unit/test_metrics_engine.py (additions)

16 new test functions (30 total):
- `test_expectancy_basic` — 6 wins + 4 losses → expectancy = 3.0 (hand-verified)
- `test_expectancy_all_wins` — 10 wins → expectancy = 9.0 (loss_rate=0 guard)
- `test_expectancy_all_losses` — 10 losses → negative expectancy (win_rate=0 guard)
- `test_min_count_gate_returns_none` — 9 trades → None
- `test_min_count_gate_boundary` — exactly 10 trades → StrategyMetrics (not None)
- `test_profit_factor_basic` — known gross wins/losses → profit_factor = 50/15
- `test_profit_factor_no_losses` — all wins → profit_factor = None (no ZeroDivisionError)
- `test_max_drawdown_amount` — cumulative [5,15,10,8,20] → max_dd = 7
- `test_drawdown_duration_hours` — peak at T+1h, trough at T+49h → 48.0 hours
- `test_drawdown_duration_still_in_drawdown` — @freeze_time("2026-01-05"), peak at T+1h, never recovered → duration ≥ 94h
- `test_drawdown_zero_drawdown` — all wins → max_drawdown_usdc=0, duration=0.0
- `test_fee_adjustment` — 10 trades × $1 fee → total_fees_usdc = $10
- `test_gross_and_net_pnl_reported` — gross=150, fees=10, net=140; net = gross - fees - funding
- `test_funding_costs_placeholder` — funding_costs_usdc == Decimal("0")
- `test_multiple_strategy_instrument_pairs` — 2×2 pairs: 3 with 10 trades (metrics), 1 with 9 (None)
- `test_compute_strategy_metrics_empty` — empty fills → empty dict

## Test Results

```
30 passed in 0.32s
```

Full unit suite: 82 passed, 0 failures.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

**funding_costs_usdc = Decimal("0")** in StrategyMetrics (intentional, documented):
- File: `libs/metrics/engine.py`, `_compute_metrics()` function
- Reason: Funding cost attribution requires position lifecycle tracking not available in Phase 10 (D-08). This is a typed placeholder, not missing functionality. The field exists in the interface and participates in `total_net_pnl` computation. Phase 13 (Claude integration) receives the zero and knows funding is not factored in.
- Resolution: METR-05/06 (future phases) will wire in actual funding cost attribution.

## Self-Check: PASSED

- libs/metrics/engine.py: FOUND
- libs/metrics/__init__.py: FOUND
- tests/unit/test_metrics_engine.py: FOUND
- Commit be8626e (RED -- failing tests + stubs): FOUND
- Commit 9c7d37e (GREEN -- implementation): FOUND
