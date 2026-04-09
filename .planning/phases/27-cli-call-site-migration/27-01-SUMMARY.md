---
phase: 27-cli-call-site-migration
plan: "01"
subsystem: libs/tuner
tags: [cli-migration, subprocess, json-extraction, anthropic-sdk-removal]
dependency_graph:
  requires: [26-01]
  provides: [CLI-01]
  affects: [libs/tuner/claude_client.py, libs/tuner/__init__.py]
tech_stack:
  added: [subprocess.run, libs.common.json_extractor]
  patterns: [CLI subprocess invocation, JSON fence extraction]
key_files:
  modified:
    - libs/tuner/claude_client.py
    - libs/tuner/__init__.py
decisions:
  - "DEFAULT_MODEL constant retained as no-op alias to avoid breaking recommender.py import"
  - "model and max_tokens params accepted but ignored — CLI uses its own model selection"
  - "TOOL_SCHEMA removed from __init__.py re-export (auto-fix, was unused outside this module)"
metrics:
  duration: ~5m
  completed: 2026-04-09
---

# Phase 27 Plan 01: CLI Call-Site Migration Summary

Replaced the Anthropic SDK `messages.create()` call in `libs/tuner/claude_client.py` with `subprocess.run(["claude", "-p", ...])` plus `extract_json()` from the Phase 26 shared utility, eliminating the `anthropic` SDK dependency from the tuner call site.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Migrate call_claude() from Anthropic SDK to subprocess.run() | 3354629 | libs/tuner/claude_client.py, libs/tuner/__init__.py |

## Decisions Made

- **DEFAULT_MODEL retained as compat alias**: `recommender.py` imports `DEFAULT_MODEL` — kept as a string constant even though the CLI ignores it. Value is `"claude-sonnet-4-5"`.
- **model/max_tokens accepted but ignored**: signature unchanged per plan; docstring updated to document ignored parameters.
- **TOOL_SCHEMA removed from __init__.py**: was a re-export only; no external callers. Auto-fixed as Rule 3 blocker.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] TOOL_SCHEMA re-export in libs/tuner/__init__.py**
- **Found during:** Task 1 verification
- **Issue:** `libs/tuner/__init__.py` imported and re-exported `TOOL_SCHEMA` from `claude_client.py`. After removing `TOOL_SCHEMA` from the module, `from libs.tuner.claude_client import ..., TOOL_SCHEMA` caused `ImportError` preventing all imports from succeeding.
- **Fix:** Removed `TOOL_SCHEMA` from the `from libs.tuner.claude_client import (...)` statement and from `__all__` in `libs/tuner/__init__.py`.
- **Files modified:** libs/tuner/__init__.py
- **Commit:** 3354629 (included in same task commit)

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|-----------|
| T-27-01 (Tampering — subprocess stdout) | `extract_json()` validates JSON structure; `isinstance(parsed, dict)` rejects list output |
| T-27-02 (DoS — subprocess hang) | `_CLI_TIMEOUT_SECONDS = 120` passed to `subprocess.run(timeout=...)` |
| T-27-03 (Info Disclosure — stderr logging) | `result.stderr[:500]` truncates stderr in log messages |

## Self-Check

```bash
[ -f "libs/tuner/claude_client.py" ] && echo "FOUND" || echo "MISSING"
git log --oneline | grep -q "3354629" && echo "COMMIT FOUND" || echo "MISSING"
```
