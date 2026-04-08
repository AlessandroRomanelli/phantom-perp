---
phase: 21-safety-critical-fixes
plan: 01
subsystem: execution, serialization, risk
tags: [safety, paper-mode, metadata, leverage, regression-tests]
requirements: [SAFE-01, SAFE-03, SAFE-04]
dependency_graph:
  requires: []
  provides: [paper-mode-skip, idea-metadata-serialization, leverage-constant-5x]
  affects: [agents/execution/main.py, libs/common/serialization.py, libs/common/constants.py, agents/risk/limits.py]
tech_stack:
  added: []
  patterns: [TDD red-green, deviation-rule-1 stale-test-fix]
key_files:
  created:
    - libs/common/tests/test_constants.py
  modified:
    - libs/common/constants.py
    - libs/common/serialization.py
    - libs/common/tests/test_serialization.py
    - agents/execution/main.py
    - agents/execution/tests/test_main.py
    - agents/risk/tests/test_limits.py
    - configs/default.yaml
    - tests/unit/test_dynamic_leverage.py
decisions:
  - "Paper mode skip placed BEFORE circuit breaker but AFTER dedup guard — ensures re-delivered paper orders deduplicate correctly without corrupting circuit breaker counters"
  - "_json_safe_metadata converts Decimal to str; risk agent already uses Decimal(str(...)) on deserialization so no downstream changes needed"
  - "configs/default.yaml trending regime caps corrected from 8.0 to 5.0 to match MAX_LEVERAGE_GLOBAL hard cap"
metrics:
  duration_seconds: 1350
  completed_date: "2026-04-08"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 9
---

# Phase 21 Plan 01: Safety Critical Fixes (SAFE-01/03/04) Summary

**One-liner:** Paper mode early-return prevents double fills; Decimal-safe metadata serialization restores funding_rate circuit breaker; MAX_LEVERAGE_GLOBAL corrected from 20.0 to 5.0 with cascading config and test fixes.

## What Was Built

Three safety-critical bugs fixed in a single TDD pass:

### SAFE-01 — Paper Mode Double Execution
**File:** `agents/execution/main.py`

Added an early-return block in `run_agent()` between the dedup guard and circuit breaker:
```python
if is_paper:
    logger.debug("paper_mode_order_skipped", order_id=order_id, note="paper_simulator handles execution")
    processed_order_ids[order_id] = None
    while len(processed_order_ids) > _DEDUP_SET_MAX:
        processed_order_ids.popitem(last=False)
    await consumer.ack(channel, "execution_agent", msg_id)
    continue
```
The execution agent is now a complete no-op in paper mode. The `paper_simulator` in the reconciliation agent handles all fill simulation.

### SAFE-03 — Metadata Lost on Redis Round-Trip
**File:** `libs/common/serialization.py`

Added `_json_safe_metadata()` helper that converts `Decimal` values to `str` (leaving `str`, `int`, `float`, `None` unchanged). Added `metadata` field to both `idea_to_dict()` (serializes) and `deserialize_idea()` (reconstructs). The risk agent's `Decimal(str(idea.metadata.get("funding_rate", ...)))` pattern is forward-compatible with `str`-serialized Decimal values.

### SAFE-04 — MAX_LEVERAGE_GLOBAL = 20.0 (should be 5.0)
**Files:** `libs/common/constants.py`, `configs/default.yaml`, `tests/unit/test_dynamic_leverage.py`

Corrected `MAX_LEVERAGE_GLOBAL = Decimal("5.0")`. Updated `configs/default.yaml` trending regime caps (8.0 → 5.0) to comply with the corrected hard cap. Fixed stale test expectations in `test_dynamic_leverage.py` that assumed the 20.0 value.

## Tests Written

| File | Tests Added | Purpose |
|------|------------|---------|
| `libs/common/tests/test_constants.py` | 5 | Regression guard for MAX_LEVERAGE_GLOBAL == 5.0 |
| `libs/common/tests/test_serialization.py` | 6 | Metadata round-trip, Decimal-to-str, empty dict |
| `agents/execution/tests/test_main.py` | 3 | Paper mode skip: structural source inspection |
| `agents/risk/tests/test_limits.py` | 1 | Leverage cap enforcement at 5.0 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stale test expectations in test_dynamic_leverage.py**
- **Found during:** Task 1 GREEN phase (full suite run)
- **Issue:** `test_route_a_all_regimes` expected `Decimal("8.0")` for `trending_up/trending_down`, which was only possible with the buggy `MAX_LEVERAGE_GLOBAL = 20.0`
- **Fix:** Updated expected values to `Decimal("5.0")` (correctly capped); added comment explaining the config value exceeds cap intentionally
- **Files modified:** `tests/unit/test_dynamic_leverage.py`
- **Commit:** 7f35126

**2. [Rule 1 - Bug] configs/default.yaml regime caps exceed corrected MAX_LEVERAGE_GLOBAL**
- **Found during:** Task 1 GREEN phase (full suite run, `test_regime_leverage_calibration.py`)
- **Issue:** `trending_up: 8.0` and `trending_down: 8.0` in `configs/default.yaml` exceeded the corrected 5.0 cap; `test_all_route_a_values_below_hard_cap` correctly caught this
- **Fix:** Updated `trending_up` and `trending_down` to `5.0` in `configs/default.yaml`
- **Files modified:** `configs/default.yaml`
- **Commit:** 7f35126

### Pre-existing Failures (Out of Scope)
Two test failures existed before this plan's changes (verified via `git stash`):
- `tests/unit/test_tuner_entrypoint.py::test_fetch_fills_calls_repository_correctly` — parameter name mismatch (`route` vs `portfolio_target`)
- `tests/unit/test_writer.py::test_apply_instrument_param_schema_a` — unrelated schema assertion

Both logged in `deferred-items.md` scope; not introduced by this plan.

## Verification Results

```
python -m pytest libs/common/tests/test_constants.py libs/common/tests/test_serialization.py \
  agents/risk/tests/test_limits.py agents/execution/tests/test_main.py -q
# 68 passed

python -c "from libs.common.constants import MAX_LEVERAGE_GLOBAL; assert MAX_LEVERAGE_GLOBAL == __import__('decimal').Decimal('5.0')"
# OK: MAX_LEVERAGE_GLOBAL == 5.0

python -m pytest --ignore=tests/unit/test_tuner_entrypoint.py -q
# 1592 passed, 5 skipped (excluding 2 pre-existing failures)
```

## Known Stubs

None — all three bugs fully wired with production logic.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced.

## Self-Check

**Commit exists:** 7f35126 — `fix(21-01): SAFE-01/03/04 — paper mode skip, metadata serialization, leverage constant`

**Files created:**
- `libs/common/tests/test_constants.py` ✓

**Files modified:**
- `libs/common/constants.py` — `MAX_LEVERAGE_GLOBAL = Decimal("5.0")` ✓
- `libs/common/serialization.py` — `_json_safe_metadata`, `metadata` in `idea_to_dict`/`deserialize_idea` ✓
- `agents/execution/main.py` — `paper_mode_order_skipped` block ✓

## Self-Check: PASSED
