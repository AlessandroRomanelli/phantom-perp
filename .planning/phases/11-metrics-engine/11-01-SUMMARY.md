---
phase: 11-metrics-engine
plan: "01"
subsystem: metrics
tags: [tdd, metrics, round-trip, vwap, fifo, pnl]
dependency_graph:
  requires: [libs/storage/repository.py (AttributedFill)]
  provides: [libs/metrics/engine.py (OrderResult, RoundTrip, vwap_aggregate, pair_round_trips, build_round_trips)]
  affects: [Phase 11 Plan 02 (metrics aggregation will consume RoundTrip)]
tech_stack:
  added: [libs/metrics/]
  patterns: [frozen dataclass with slots=True, FIFO deque pairing, VWAP aggregation]
key_files:
  created:
    - libs/metrics/__init__.py
    - libs/metrics/engine.py
    - tests/unit/test_metrics_engine.py
  modified: []
decisions:
  - "FIFO deque uses appendleft/pop (left=top, right=oldest) for correct FIFO ordering"
  - "Zero-size fills filtered before VWAP denominator to guard against ZeroDivisionError"
  - "build_round_trips excludes keys with no closed round-trips (open-only groups omitted)"
metrics:
  duration_minutes: 3
  completed_date: "2026-03-25"
  tasks_completed: 2
  files_changed: 3
---

# Phase 11 Plan 01: Metrics Engine Foundation Summary

## One-liner

VWAP aggregation and FIFO round-trip reconstruction pipeline producing fee-adjusted P&L from raw AttributedFill rows, with full TDD coverage.

## What Was Built

### libs/metrics/engine.py

Two frozen dataclasses and three pipeline functions:

**OrderResult** — Aggregated single-order result from partial fills. Fields: `order_id`, `instrument`, `primary_source`, `side`, `avg_price` (Decimal), `total_size` (Decimal), `total_fee` (Decimal), `filled_at`.

**RoundTrip** — Completed trade (entry + exit pair). Fields: `entry_order_id`, `exit_order_id`, `instrument`, `primary_source`, `side` (entry side), `entry_price`, `exit_price`, `size`, `gross_pnl`, `total_fees`, `net_pnl`, `opened_at`, `closed_at`.

**vwap_aggregate(fills)** — VWAP-aggregates partial fills for one order. Filters zero-size fills before computing weighted average. Sums fees across all fills. Uses latest fill timestamp.

**pair_round_trips(orders)** — FIFO deque matching. Opposite-side order closes the oldest open entry. Direction logic: BUY entry → LONG gross_pnl = (exit - entry) * size; SELL entry → SHORT gross_pnl = (entry - exit) * size. A BUY order correctly closes a SHORT when the FIFO stack top has side="SELL". Unmatched entries (open positions) are excluded per D-04.

**build_round_trips(fills)** — Groups fills by (primary_source, instrument), VWAP-aggregates per order_id, applies defensive sort by filled_at, calls pair_round_trips. Returns dict keyed by (source, instrument); empty groups excluded.

### libs/metrics/__init__.py

Public re-exports of all five symbols with `__all__`.

### tests/unit/test_metrics_engine.py

14 unit tests covering:
- VWAP with 3 partial fills (100.2 weighted average)
- VWAP single fill passthrough
- Zero-size guard (no ZeroDivisionError)
- LONG round-trip (BUY→SELL)
- SHORT round-trip (SELL→BUY with explicit "BUY closes short" comment)
- Multiple sequential round-trips (FIFO order preserved)
- Overlapping entries / pyramiding (2x BUY before 2x SELL → FIFO pairing)
- Open position exclusion (trailing unmatched entry excluded)
- LONG P&L: gross=10, fees=1, net=9
- SHORT P&L: entry 110, exit 100, gross=10
- Zero gross P&L → negative net (fees make breakeven a loss)
- build_round_trips groups by (source, instrument)
- build_round_trips empty input → empty dict
- build_round_trips sorts fills chronologically before pairing

## Test Results

```
14 passed in 0.22s
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all functions are fully implemented.

## Self-Check: PASSED

- libs/metrics/__init__.py: FOUND
- libs/metrics/engine.py: FOUND
- tests/unit/test_metrics_engine.py: FOUND
- Commit 0a75798 (RED): FOUND
- Commit ed955a3 (GREEN): FOUND
