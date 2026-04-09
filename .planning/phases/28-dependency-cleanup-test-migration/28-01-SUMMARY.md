---
phase: 28-dependency-cleanup-test-migration
plan: "01"
subsystem: tuner
tags: [dependency-cleanup, test-migration, anthropic-removal]

dependency_graph:
  requires: []
  provides: [anthropic-free-pyproject, subprocess-based-tuner-tests]
  affects: [pyproject.toml, tests/unit/test_claude_client.py, scripts/manual_tune.py]

tech_stack:
  added: []
  patterns: [subprocess-mock-testing]

key_files:
  created: []
  modified:
    - pyproject.toml
    - tests/unit/test_claude_client.py
    - scripts/manual_tune.py

decisions:
  - id: D-28-01
    summary: "Removed anthropic from both main dependencies and tuner optional-dependencies group"
  - id: D-28-02
    summary: "Replaced 5 Anthropic SDK call_claude tests with 7 subprocess.run mock tests covering all error paths"
  - id: D-28-03
    summary: "Dropped TOOL_SCHEMA import from manual_tune.py (no longer exists in production code)"

metrics:
  duration_minutes: 8
  completed_date: "2026-04-09T11:11:26Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 28 Plan 01: Anthropic Dependency Removal and Tuner Test Migration Summary

Remove the anthropic SDK from pyproject.toml entirely and rewrite the tuner test suite to mock `subprocess.run` instead of `anthropic.Anthropic`, eliminating the SDK as a runtime and test dependency.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Remove anthropic from pyproject.toml and manual_tune.py | 907d6bf | pyproject.toml, scripts/manual_tune.py |
| 2 | Rewrite test_claude_client.py to mock subprocess | c6e784c | tests/unit/test_claude_client.py |

## What Was Built

**Task 1 — Dependency removal (DEP-01, DEP-02):**
- Removed `"anthropic>=0.86,<1"` from `dependencies` list in pyproject.toml (line 20)
- Removed `"anthropic>=0.86,<1"` from `[project.optional-dependencies] tuner` group (line 49)
- Removed `import anthropic` and `TOOL_SCHEMA` import from `scripts/manual_tune.py`
- Confirmed no `ANTHROPIC_API_KEY` references in Dockerfiles, docker-compose.yml, or `.env` templates (DEP-02)

**Task 2 — Test migration (DEP-03 tuner side):**
- Removed 4 TOOL_SCHEMA tests (schema no longer exists in production)
- Removed 5 `patch("anthropic.Anthropic")` call_claude tests
- Added 7 subprocess-based call_claude tests:
  - `test_call_claude_returns_tool_input_on_success` — happy path via `subprocess.run` mock
  - `test_call_claude_passes_prompt_to_cli` — verifies `["claude", "-p", ...]` args and `timeout=120`
  - `test_call_claude_returns_none_on_timeout` — `subprocess.TimeoutExpired` handling
  - `test_call_claude_returns_none_on_nonzero_exit` — non-zero returncode handling
  - `test_call_claude_returns_none_on_invalid_json` — `JsonExtractionError` path
  - `test_call_claude_returns_none_on_oserror` — `OSError` (claude binary missing)
  - `test_call_claude_returns_none_on_non_dict_response` — list result rejection
- Preserved all 7 `build_system_prompt` / `build_user_message` tests unchanged
- Preserved `test_default_model_is_correct` unchanged
- **Final count: 15 tests, all passing**

## Verification

```
pytest tests/unit/test_claude_client.py -x -v
15 passed in 0.26s
```

```
grep -r "anthropic" pyproject.toml → no matches (EXIT 1)
grep "import anthropic" scripts/manual_tune.py → no matches (EXIT 1)
grep -r "ANTHROPIC_API_KEY" --include="Dockerfile" --include="*.yml" --include=".env*" → no matches (EXIT 0)
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

None — this plan only removes a dependency and rewrites tests. No new network endpoints, auth paths, or schema changes were introduced.

## Self-Check: PASSED

- pyproject.toml exists and has zero anthropic references: FOUND
- scripts/manual_tune.py exists with no anthropic import: FOUND
- tests/unit/test_claude_client.py exists with 15 passing tests: FOUND
- Commit 907d6bf exists: FOUND
- Commit c6e784c exists: FOUND
