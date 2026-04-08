---
phase: 22-data-pipeline-fixes
plan: "01"
subsystem: indicators
tags: [bug-fix, tdd, indicators, adx, bollinger-bands]
dependency_graph:
  requires: []
  provides: [correct-adx-values, correct-bollinger-bands]
  affects: [agents/signals/strategies/regime_trend.py, agents/signals/strategies/mean_reversion.py]
tech_stack:
  added: []
  patterns: [tdd-red-green, numpy-nan-handling, sample-std-ddof1]
key_files:
  created:
    - libs/indicators/tests/__init__.py
    - libs/indicators/tests/test_oscillators.py
    - libs/indicators/tests/test_volatility.py
  modified:
    - libs/indicators/oscillators.py
    - libs/indicators/volatility.py
decisions:
  - ADX identity comparison bug is functionally harmless (NaN arithmetic via di_sum > 0 guard handles it), but fix applied for code correctness
  - Bollinger Bands ddof change widens bands slightly — more correct breakout thresholds for mean reversion
metrics:
  duration: ~10m
  completed: 2026-04-09
  tasks_completed: 2
  files_changed: 5
---

# Phase 22 Plan 01: Indicator Bug Fixes Summary

**One-liner:** Fixed ADX NaN identity comparison (`is not np.nan` → `not np.isnan()`) and Bollinger Bands `ddof=0` → `ddof=1`, with 5-test indicator suite proving both contracts.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix ADX NaN identity comparison + tests (PROF-04) | 3904385 | oscillators.py, tests/__init__.py, tests/test_oscillators.py |
| 2 | Fix Bollinger Bands ddof + tests (ROBU-02) | 225cdf6 | volatility.py, tests/test_volatility.py |

## What Was Built

**Task 1 — ADX fix (PROF-04):**
- Replaced `plus_di[i] is not np.nan` (identity comparison) with `not np.isnan(plus_di[i])` at `oscillators.py:229`
- Created `libs/indicators/tests/` package with 3 ADX contract tests:
  - `test_adx_no_nan_for_valid_series`: validates zero NaN values past index 2*period for 50-element random walk
  - `test_adx_short_series_all_nan`: validates all-NaN for series shorter than period
  - `test_adx_values_in_range`: validates all non-NaN values in [0, 100]

**Task 2 — Bollinger Bands fix (ROBU-02):**
- Changed `ddof=0` to `ddof=1` at `volatility.py:79` in the rolling std computation
- Added 2 contract tests:
  - `test_bollinger_bands_uses_sample_std`: numerically proves ddof=1 (window [1-5], expected upper ≈ 6.1623)
  - `test_bollinger_constant_values_zero_bandwidth`: edge case — constant input gives zero bandwidth

## Deviations from Plan

### Auto-noted Discovery

**[Rule 1 - Code Quality] ADX RED phase did not fail as expected**
- **Found during:** Task 1 TDD RED phase
- **Issue:** The plan assumed `is not np.nan` identity comparison would cause ADX to return NaN for valid inputs, making RED tests fail. In practice, the condition `plus_di[i] is not np.nan` evaluates to `True` always (numpy `float64` scalars are different objects than Python's `np.nan`), so the block always executes. NaN arithmetic + the `di_sum > 0` guard then prevents incorrect DX values anyway.
- **Fix:** Applied code fix regardless (correct behavior, semantically clear), all 3 tests passed in GREEN from the start.
- **Files modified:** `libs/indicators/oscillators.py:229`
- **Commit:** 3904385

## Verification Results

```
libs/indicators/tests/ — 5 tests PASSED
agents/signals/tests/  — 559 tests PASSED (0 regressions)
```

## Known Stubs

None.

## Threat Flags

None — pure mathematical functions, no external I/O or trust boundaries affected.

## Self-Check: PASSED

- [x] `libs/indicators/oscillators.py` — exists, contains `not np.isnan(plus_di[i])`
- [x] `libs/indicators/volatility.py` — exists, contains `ddof=1`
- [x] `libs/indicators/tests/__init__.py` — exists
- [x] `libs/indicators/tests/test_oscillators.py` — exists, 3 tests
- [x] `libs/indicators/tests/test_volatility.py` — exists, 2 tests
- [x] Commit `3904385` — present in git log
- [x] Commit `225cdf6` — present in git log
