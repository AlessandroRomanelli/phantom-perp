---
phase: 17-bug-fixes
plan: "01"
subsystem: risk-agent
tags: [bug-fix, paper-mode, position-deserialization, risk-guards]
dependency_graph:
  requires: [16-02]
  provides: [BUG-01-fix, BUG-03-verification]
  affects: [agents/risk/main.py]
tech_stack:
  added: []
  patterns: [TDD, module-private-helper, Redis-stream-deserialization]
key_files:
  created: []
  modified:
    - agents/risk/main.py
    - agents/risk/tests/test_risk_engine.py
decisions:
  - "_perp_position_from_dict placed as module-private helper before PaperPortfolioStateFetcher — keeps deserialization logic co-located with the class that uses it and avoids cross-module dependency"
  - "Derived initial_margin/maintenance_margin from size*mark_price/leverage — only fields available in the stream payload; full margin tracking deferred to live mode reconciliation"
  - "BUG-03 (reduce_only parsing) confirmed resolved by Phase 16 centralized serialization — no additional code changes needed"
metrics:
  duration_minutes: 8
  completed_date: "2026-04-08"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 2
---

# Phase 17 Plan 01: Paper Mode Position Deserialization Fix Summary

**One-liner:** Fixed `PaperPortfolioStateFetcher.fetch()` to deserialize Redis stream positions into `PerpPosition` objects via `_perp_position_from_dict`, enabling max_concurrent and same-instrument guards in paper mode.

## What Was Built

### Task 1: Fix PaperPortfolioStateFetcher to deserialize positions (BUG-01)

**TDD RED** (`a2b718e`): Added 6 failing tests across two new test classes:
- `TestPerpPositionFromDict` — 4 unit tests for the helper (valid LONG, valid SHORT, missing pnl defaults zero, invalid side raises)
- `TestPaperPositionsInRiskGuards` — 2 integration tests (max_concurrent blocks, same-instrument stacking rejected)

**TDD GREEN** (`010af77`): Implemented the fix in `agents/risk/main.py`:
- Added `_perp_position_from_dict(d, route) -> PerpPosition` module-private helper before `PaperPortfolioStateFetcher`. Parses side via `PositionSide(d["side"])`, Decimal fields directly, derives `initial_margin = size * mark_price / leverage`, `maintenance_margin = initial_margin / 2`, `margin_ratio = float(maint_margin / initial_margin)`. Fields absent from stream (`realized_pnl_usdc`, `cumulative_funding_usdc`, `total_fees_usdc`) default to `Decimal("0")`.
- Replaced `positions=[]` (line 120) with a list comprehension calling `_perp_position_from_dict` for each entry in `data.get("positions", [])` where `size > 0`.

All 6 new tests pass. Full risk engine suite (33 tests) passes with no regressions.

### Task 2: Verify BUG-03 resolution and run full regression

Pure verification — no code changes:
- `TestParseBool` (12 tests): all pass — Phase 16 `_parse_bool()` still intact
- `reduce_only` filter (4 tests): all pass — centralized deserialization handles all string representations
- Full risk engine suite excluding pre-existing failure: 33 passed

## Deviations from Plan

None — plan executed exactly as written. TDD RED/GREEN flow followed. `PositionSide` was already imported in `main.py` (confirmed at line 34), so no new import needed.

## Known Stubs

None. Position deserialization is fully wired. Fields not in the stream payload (`realized_pnl_usdc`, `cumulative_funding_usdc`, `total_fees_usdc`) default to `Decimal("0")` which is correct for paper mode — the paper simulator does not track these.

## Threat Flags

No new external surface introduced. `_perp_position_from_dict` operates on internal Redis stream data only. Threat T-17-03 (DoS via malformed dict) is mitigated by the existing `try/except Exception` in `PaperPortfolioStateFetcher.fetch()` which falls back to `self._defaults[target]` on any parse error — no crash possible.

## Self-Check: PASSED

- `agents/risk/main.py` modified: confirmed (`_perp_position_from_dict` present, `data.get("positions", [])` present)
- `agents/risk/tests/test_risk_engine.py` modified: confirmed (`TestPerpPositionFromDict`, `TestPaperPositionsInRiskGuards` present)
- Commits exist:
  - `a2b718e` — TDD RED tests
  - `010af77` — TDD GREEN implementation
- All 6 new tests pass, 33 total risk engine tests pass (excluding pre-existing unrelated failure)
