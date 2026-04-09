---
phase: 27-cli-call-site-migration
plan: "02"
subsystem: agents/signals
tags: [cli-migration, subprocess, anthropic-sdk-removal, signal-generation]
dependency_graph:
  requires: [26-01, 26-02]
  provides: [CLI-02, CLI-03]
  affects: [agents/signals/claude_scheduler.py, agents/signals/orch_scheduler.py]
tech_stack:
  added: [asyncio.create_subprocess_exec, libs.common.json_extractor]
  patterns: [async-subprocess, timeout-guard, json-fence-extraction]
key_files:
  created:
    - libs/common/json_extractor.py
  modified:
    - agents/signals/claude_market_client.py
    - agents/signals/orch_client.py
decisions:
  - "Used asyncio.create_subprocess_exec (not asyncio.run_in_executor) — signals agent runs under asyncio; subprocess is the correct primitive"
  - "Timeout 90s for market analysis (latency-sensitive), 120s for orchestrator (larger context)"
  - "Created libs/common/json_extractor.py in this worktree — Phase 26-01 dependency not yet on this branch"
  - "Applied Phase 26-02 JSON output format instructions to both system prompts in same commit as SDK removal"
  - "ORCHESTRATOR_TOOL removed entirely — Phase 28 handles test migration"
metrics:
  duration_minutes: 12
  completed_date: "2026-04-09"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 27 Plan 02: CLI Call Site Migration — Signals Agent Summary

**One-liner:** Replaced `anthropic.AsyncAnthropic()` SDK calls in both signals agent call sites with `asyncio.create_subprocess_exec("claude", "-p", ...)` + `extract_json()` JSON fence parsing, eliminating the anthropic SDK dependency from the live trading path.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Migrate call_claude_analysis() to asyncio subprocess | 2206d15 | agents/signals/claude_market_client.py, libs/common/json_extractor.py |
| 2 | Migrate call_claude_orchestrator() to asyncio subprocess | 709ab0e | agents/signals/orch_client.py |

## What Was Built

**Task 1 — `call_claude_analysis()` migration:**
- Removed `import anthropic`, `import os`, `MARKET_ANALYSIS_TOOL` tool schema dict, `_MODEL`, `_MAX_TOKENS` constants
- Removed `ANTHROPIC_API_KEY` environment variable check at function entry
- Added `_CLI_TIMEOUT_SECONDS: int = 90` — market analysis is latency-sensitive
- Rewrote the API call block to use `asyncio.create_subprocess_exec("claude", "-p", full_prompt)` with `asyncio.wait_for(..., timeout=90)`
- CLI stdout parsed via `extract_json()` from `libs.common.json_extractor`
- Applied Phase 26-02 Output Format JSON schema block to `build_system_prompt()` (rule 9 updated)
- All helpers (`validate_claude_response`, `build_market_context`, ATR helpers, tick-rounding) unchanged

**Task 2 — `call_claude_orchestrator()` migration:**
- Removed `import anthropic`, `import os`, `ORCHESTRATOR_TOOL` tool schema dict, `_MODEL` constant
- Removed `ANTHROPIC_API_KEY` environment variable check at function entry
- Added `_CLI_TIMEOUT_SECONDS: int = 120` — orchestrator context is larger
- Rewrote the API call block to use `asyncio.create_subprocess_exec("claude", "-p", full_prompt)` with `asyncio.wait_for(..., timeout=120)`
- CLI stdout parsed via `extract_json()` — extracts `decisions` list from top-level dict
- Applied Phase 26-02 Output Format JSON schema block to `_build_orchestrator_system_prompt()` (rule 8 updated)
- `validate_orchestrator_response()`, `build_orchestrator_context()`, `OrchestratorParams` unchanged

**Dependency created:**
- `libs/common/json_extractor.py` was added to this worktree — it exists on the Phase 26 branch but is not yet merged to the base this worktree was cut from. Content is identical to the Phase 26-01 implementation.

## Verification Results

All 6 plan verification checks passed:
1. `from agents.signals.claude_market_client import call_claude_analysis` — OK
2. `from agents.signals.orch_client import call_claude_orchestrator` — OK
3. `grep -c "import anthropic"` returns 0 for both files
4. `create_subprocess_exec` present in both files
5. `from agents.signals.claude_scheduler import run_claude_scheduler` — OK (caller intact)
6. `from agents.signals.orch_scheduler import run_orchestrator_scheduler` — OK (caller intact)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] libs/common/json_extractor.py missing in this worktree**
- **Found during:** Task 1 setup
- **Issue:** Phase 26-01 created `libs/common/json_extractor.py` on a parallel worktree branch that had not been merged to the base (`65ae921`) this worktree was cut from. Both `call_claude_analysis()` and `call_claude_orchestrator()` import `extract_json` — the import would fail at runtime without this file.
- **Fix:** Created `libs/common/json_extractor.py` with identical content to the Phase 26-01 implementation (verified against `git show 5e14f4a`). Committed in Task 1 commit.
- **Files modified:** `libs/common/json_extractor.py` (created)
- **Commit:** 2206d15

**2. [Rule 2 - Missing critical functionality] Phase 26-02 JSON output format instructions absent from system prompts**
- **Found during:** Task 1
- **Issue:** The plan states "prompts already have Output Format instructions from Phase 26" but this worktree predates those commits. Without the JSON output format block in the system prompt, the CLI call would produce unstructured prose and `extract_json()` would always raise `JsonExtractionError`.
- **Fix:** Applied Phase 26-02 diff to both `build_system_prompt()` (rule 9 updated, JSON schema appended) and `_build_orchestrator_system_prompt()` (rule 8 updated, JSON schema appended). Applied in same commits as the SDK removal.
- **Files modified:** `agents/signals/claude_market_client.py`, `agents/signals/orch_client.py`
- **Commits:** 2206d15, 709ab0e

## Known Stubs

None — both call sites wire directly to the Claude CLI subprocess and parse real output.

## Threat Flags

All threat register items from the plan's `<threat_model>` are mitigated:

| Threat | Mitigation Applied |
|--------|--------------------|
| T-27-04 Tampering (market stdout) | `extract_json()` validates JSON; `validate_claude_response()` validates prices/direction |
| T-27-05 Tampering (orch stdout) | `extract_json()` validates JSON; `validate_orchestrator_response()` clips against bounds.yaml |
| T-27-06 DoS (subprocess hang) | `asyncio.wait_for` with 90s / 120s timeouts |
| T-27-07 Info disclosure (stderr) | stderr truncated to 500 chars in error logs |

## Self-Check

All commits verified:
- `libs/common/json_extractor.py` — created, present in commit 2206d15
- `agents/signals/claude_market_client.py` — modified in commit 2206d15
- `agents/signals/orch_client.py` — modified in commit 709ab0e

## Self-Check: PASSED
