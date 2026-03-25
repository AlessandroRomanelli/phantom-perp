---
phase: 12-safety-bounds
plan: "02"
subsystem: tuner
tags: [atomic-write, yaml-writer, tdd, safety-layer, os-replace, post-write-validation]
dependency_graph:
  requires:
    - phase: 12-01
      provides: "libs/tuner/bounds.py (validate_value, BoundsEntry), libs/tuner/audit.py (ParameterChange, log_parameter_change), configs/bounds.yaml"
  provides:
    - "libs/tuner/writer.py: apply_parameter_changes() with atomic write, post-write validation, rollback"
    - "Schema A and Schema B instrument config format support"
    - "Exact byte-for-byte rollback on post-write validation mismatch"
  affects: [13-claude-advisor]
tech-stack:
  added: []
  patterns:
    - "atomic-write-os-replace: write to same-directory temp file, then os.replace for atomic swap"
    - "post-write-validation: re-parse YAML after write, compare with intended dict, rollback if mismatch"
    - "schema-detection: A=nested parameters: dict, B=bare keys at instrument level"
    - "bounds-before-disk: validate all values against registry before any file I/O"
    - "exact-rollback: store original_bytes before any write; restore with _write_bytes_atomic on failure"

key-files:
  created:
    - libs/tuner/writer.py
    - tests/unit/test_writer.py
  modified:
    - libs/tuner/__init__.py

key-decisions:
  - "Backup via copy.deepcopy(current_data) instead of yaml round-trip to keep safe_load call count predictable for post-write validation mocking"
  - "Rollback restores exact original bytes (read_bytes before write) rather than yaml round-trip to preserve formatting, comments, and quote styles"
  - "_write_bytes_atomic as separate function from _write_atomic to handle bytes vs dict cleanly"
  - "TC003 (Path in TYPE_CHECKING): Path moved to TYPE_CHECKING block since from __future__ import annotations enables lazy evaluation of annotations"

patterns-established:
  - "Apply bounds validation as first step before any I/O (fail fast, no side effects)"
  - "Schema detection via _detect_instrument_schema enables transparent A/B handling in one writer"
  - "Weight changes routed to strategy.weight, not parameters.weight"
  - "Missing instruments block created with Schema A format for new instrument entries"

requirements-completed: [SAFE-02, SAFE-03]

duration: "7 minutes"
completed: "2026-03-25"
---

# Phase 12 Plan 02: Atomic YAML Writer Summary

**apply_parameter_changes() with atomic os.replace writes, Schema A/B instrument detection, bounds validation before disk I/O, post-write re-parse verification, and exact byte-for-byte rollback on mismatch.**

## Performance

- **Duration:** 7 minutes
- **Started:** 2026-03-25T13:23:57Z
- **Completed:** 2026-03-25T13:30:46Z
- **Tasks:** 1 (TDD with RED/GREEN/REFACTOR)
- **Files modified:** 3

## Accomplishments

- Atomic YAML writer via `os.replace` with same-directory temp file (same-filesystem guarantee)
- Post-write validation re-parses YAML and compares against intended dict — mismatches trigger rollback
- Rollback restores exact original bytes to preserve file formatting, quotes, and comments
- Schema A (nested `parameters:`) and Schema B (bare keys) both detected and written correctly
- Missing `instruments:` block created with Schema A format when absent (funding_arb case)
- All 48 tests pass: 19 bounds + 23 writer + 6 audit

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests** - `4ad9c01` (test)
2. **Task 1 GREEN+REFACTOR: writer.py implementation** - `a03dde2` (feat)

**Plan metadata:** (docs commit follows)

_Note: TDD task split into RED commit then GREEN+REFACTOR commit_

## Files Created/Modified

- `libs/tuner/writer.py` - apply_parameter_changes() with all atomic write, validation, rollback logic
- `libs/tuner/__init__.py` - added apply_parameter_changes to exports and __all__
- `tests/unit/test_writer.py` - 23 tests covering all acceptance criteria

## Decisions Made

- **backup via deepcopy not yaml round-trip**: `yaml.safe_load(yaml.safe_dump(...))` would add a third `safe_load` call making the post-write mock intercept the wrong call; `copy.deepcopy(current_data)` avoids the extra call entirely
- **exact byte rollback**: Storing `original_bytes = strategy_path.read_bytes()` before any write ensures rollback restores the exact original file (comments, quoted strings, formatting preserved) rather than a yaml-reformatted version
- **`_write_bytes_atomic` as separate helper**: Rollback writes raw bytes while forward write serializes a dict — clean separation avoids overloading one function with both modes

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed backup strategy to preserve exact original file bytes**
- **Found during:** Task 1 GREEN phase (test_post_write_validation_rollback)
- **Issue:** Plan specified `backup_data = yaml.safe_load(yaml.safe_dump(current_data, ...))` but this (a) added an extra `safe_load` call that broke the post-write mock test, and (b) would produce different bytes from the original (yaml.safe_dump strips quotes from strings like `"momentum"` → `momentum`)
- **Fix:** Replaced yaml round-trip backup with `original_bytes = strategy_path.read_bytes()` captured before write, plus `_write_bytes_atomic()` helper for raw byte restoration
- **Files modified:** libs/tuner/writer.py
- **Verification:** test_post_write_validation_rollback passes; original_content assertion confirms byte-for-byte restore
- **Committed in:** a03dde2 (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug in backup strategy)
**Impact on plan:** Fix strengthens the implementation — exact byte restore is strictly better than yaml round-trip. No scope creep.

## Issues Encountered

- ruff TC003: `from pathlib import Path` triggered "move to TYPE_CHECKING block" — resolved by moving Path import to `if TYPE_CHECKING` block (valid since `from __future__ import annotations` is present, enabling lazy annotation evaluation). This was also a pre-existing issue in bounds.py but is out of scope for this plan.

## Known Stubs

None — all implemented functionality is wired and functional.

## Next Phase Readiness

- `apply_parameter_changes()` is ready for Phase 13 (Claude advisor) to call with strategy_path, changes, instrument_changes, and the bounds registry
- Full import: `from libs.tuner import apply_parameter_changes` works
- Bounds registry from Phase 12-01 is consumed correctly
- ParameterChange records returned for audit logging integration

## Self-Check: PASSED

All created files verified present. Both commits (4ad9c01, a03dde2) confirmed in git log.

---
*Phase: 12-safety-bounds*
*Completed: 2026-03-25*
