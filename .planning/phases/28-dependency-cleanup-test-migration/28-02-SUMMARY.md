---
phase: 28-dependency-cleanup-test-migration
plan: "02"
subsystem: signals/tests
tags: [testing, dependency-cleanup, subprocess, anthropic-removal]
dependency_graph:
  requires: []
  provides: [DEP-03-signals-side]
  affects: [agents/signals/tests/test_claude_market_analysis.py, agents/signals/tests/test_orch_client.py]
tech_stack:
  added: []
  patterns: [asyncio.create_subprocess_exec mock, _mock_cli_response helper, json code-block stdout simulation]
key_files:
  modified:
    - agents/signals/tests/test_claude_market_analysis.py
    - agents/signals/tests/test_orch_client.py
decisions:
  - "Replaced anthropic SDK mocking with asyncio.create_subprocess_exec patching at 'agents.signals.claude_market_client.asyncio.create_subprocess_exec' and 'agents.signals.orch_client.asyncio.create_subprocess_exec'"
  - "Retained all non-SDK test classes unchanged (TestValidateClaudeResponse, TestBuildMarketContext, TestProcessInstrument, TestBuildOrchestratorContext, TestValidateOrchestratorResponse, TestOrchestratorParams, TestBuildOrchestratorContextNewsSection)"
  - "Added _mock_cli_response() helper in each file to produce ```json code block stdout bytes"
  - "Removed TestOrchestratorTool (7), TestOrchestratorToolConfidenceSchema (3), TestCallClaudeOrchestratorTokenLogging (1) — test removed constants and SDK-only behavior"
metrics:
  duration_minutes: 8
  completed_date: "2026-04-09"
  tasks_completed: 2
  files_modified: 2
---

# Phase 28 Plan 02: Signals Test Migration to Subprocess Mocking Summary

Migrated both signals-side async test files from mocking the Anthropic SDK to mocking `asyncio.create_subprocess_exec`, completing DEP-03 for the signals side. All tests now pass without the `anthropic` package installed.

## What Was Built

Both test files were rewritten to remove all Anthropic SDK dependencies:

**test_claude_market_analysis.py** (35 tests, was ~32):
- Removed: `import anthropic`, `import httpx`, `MARKET_ANALYSIS_TOOL` import, `_make_tool_use_block`, `_make_claude_response` helpers, `test_market_analysis_tool_schema`
- Fixed: `test_build_system_prompt_returns_string` asserts `"JSON" in prompt` instead of `"submit_market_analysis" in prompt`
- Rewrote: `TestCallClaudeAnalysis` (5 → 6 tests) — now patches `asyncio.create_subprocess_exec` with a `_mock_cli_response()` helper that returns `json` in a ` ```json ``` ` code block
- Added: `test_cli_oserror_returns_none` (new coverage)

**test_orch_client.py** (45 tests, was 46):
- Removed: `ORCHESTRATOR_TOOL` import, `_make_tool_use_block`, `_make_claude_response` helpers
- Removed classes: `TestOrchestratorTool` (7), `TestOrchestratorToolConfidenceSchema` (3), `TestCallClaudeOrchestratorTokenLogging` (1) — all tested removed SDK constants or SDK-only logging behavior
- Rewrote: `TestCallClaudeOrchestrator` (5 tests) — now patches `asyncio.create_subprocess_exec` with `_mock_cli_response()` helper
- Replaced: `test_missing_api_key_returns_none` → `test_cli_timeout_returns_none`, `test_api_error_returns_none` → `test_cli_nonzero_exit_returns_none`, `test_missing_tool_use_block_returns_none` → `test_cli_invalid_json_returns_none`

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | `1305f3c` | rewrite test_claude_market_analysis to mock subprocess |
| Task 2 | `f1fd7de` | rewrite test_orch_client to mock subprocess |

## Deviations from Plan

None — plan executed exactly as written.

## Verification

```
grep -r "import anthropic" agents/signals/tests/test_claude_market_analysis.py agents/signals/tests/test_orch_client.py
# → no matches

grep -r "ANTHROPIC_API_KEY" agents/signals/tests/test_claude_market_analysis.py agents/signals/tests/test_orch_client.py
# → no matches

grep -r "MARKET_ANALYSIS_TOOL\|ORCHESTRATOR_TOOL" agents/signals/tests/test_claude_market_analysis.py agents/signals/tests/test_orch_client.py
# → no matches

python3 -m pytest agents/signals/tests/test_claude_market_analysis.py agents/signals/tests/test_orch_client.py -x
# → 80 passed
```

## Known Stubs

None.

## Threat Flags

None — test-only changes, no new trust boundaries.

## Self-Check: PASSED

- agents/signals/tests/test_claude_market_analysis.py: FOUND
- agents/signals/tests/test_orch_client.py: FOUND
- commit 1305f3c: FOUND
- commit f1fd7de: FOUND
