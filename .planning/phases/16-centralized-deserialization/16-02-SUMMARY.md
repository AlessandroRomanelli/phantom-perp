---
phase: 16-centralized-deserialization
plan: "02"
subsystem: agents
tags: [serialization, refactor, migration, deserialization]
dependency_graph:
  requires: ["16-01"]
  provides: ["centralized-deserialization-complete"]
  affects: ["all-agents", "agent-tests"]
tech_stack:
  added: []
  patterns: ["centralized-serialization-import", "import-from-libs-common-serialization"]
key_files:
  modified:
    - agents/execution/main.py
    - agents/risk/main.py
    - agents/alpha/main.py
    - agents/signals/main.py
    - agents/confirmation/main.py
    - agents/reconciliation/main.py
    - agents/reconciliation/paper_simulator.py
    - agents/monitoring/main.py
    - agents/ingestion/normalizer.py
    - agents/ingestion/main.py
    - agents/execution/tests/test_main.py
    - agents/risk/tests/test_risk_engine.py
    - agents/alpha/tests/test_main.py
    - agents/signals/tests/test_main.py
    - agents/confirmation/tests/test_main.py
    - agents/reconciliation/tests/test_main.py
    - agents/monitoring/tests/test_main.py
decisions:
  - "confirmation/main.py local deserialize_order renamed to deserialize_proposed_order at call site to match shared module canonical name"
  - "signals/main.py keeps _json_safe locally — numpy is not a libs dependency; applied after calling shared signal_to_dict"
  - "normalizer.py snapshot_to_dict removed; ingestion/main.py import updated from normalizer to libs.common.serialization"
  - "monitoring/main.py alert_to_dict and deserialize_alert kept local — Alert model lives in agents/monitoring/alerting.py, not in libs/common/models"
metrics:
  duration: "~25 min"
  completed: "2026-04-08"
  tasks_completed: 2
  files_modified: 17
---

# Phase 16 Plan 02: Centralized Deserialization Migration Summary

Migrated all 9 agent/normalizer source files and 7 test files to use `libs.common.serialization` as the single source of truth for all Redis stream serialization. Eliminated ~614 lines of duplicated agent-local deserialization code, replacing with 50 lines of import statements.

## What Was Built

All 7 agents and the ingestion normalizer now import serialization functions from `libs.common.serialization` instead of defining local copies. This completes BUG-04: no agent-local deserialization copies remain (except the intentionally-local alert pair in monitoring).

## Tasks Completed

### Task 1: Migrate agent source files (commit fa5c839)

For each of 9 files:
- Removed all local `deserialize_*` and `*_to_dict` functions
- Added `from libs.common.serialization import ...` with the exact functions needed
- Updated one call site in `confirmation/main.py`: `deserialize_order(payload)` → `deserialize_proposed_order(payload)`
- Updated one call site in `signals/main.py`: wrapped `signal_to_dict(signal)` with `_json_safe` applied to the returned metadata dict

**Files modified:** execution/main.py, risk/main.py, alpha/main.py, signals/main.py, confirmation/main.py, reconciliation/main.py, reconciliation/paper_simulator.py, monitoring/main.py, ingestion/normalizer.py, ingestion/main.py

### Task 2: Update test imports (commit 59b103a)

Updated 7 test files to import from `libs.common.serialization` rather than from agent modules:
- Moved top-level imports (e.g. `from agents.alpha.main import deserialize_signal`) to `libs.common.serialization`
- Removed all inline method-level cross-agent imports (e.g. `from agents.risk.main import order_to_dict`)
- Renamed `deserialize_order` → `deserialize_proposed_order` in confirmation test
- Full suite: **1205 passed, 5 skipped** (5 skips are pre-existing, unrelated to this plan)

**Files modified:** execution/tests/test_main.py, risk/tests/test_risk_engine.py, alpha/tests/test_main.py, signals/tests/test_main.py, confirmation/tests/test_main.py, reconciliation/tests/test_main.py, monitoring/tests/test_main.py

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Cleanup] Removed unused imports after function deletion**
- **Found during:** Task 1 (all agents)
- **Issue:** Removing local serialization functions left behind unused stdlib imports (`datetime`, `Decimal`, `Any`, `OrderSide`, `PositionSide`, etc.) that were only used in the now-deleted functions
- **Fix:** Removed each unused import from each agent, keeping only what's still used in the body
- **Files modified:** execution/main.py, alpha/main.py, confirmation/main.py, reconciliation/main.py, reconciliation/paper_simulator.py, monitoring/main.py, ingestion/normalizer.py

**2. [Rule 1 - Bug] confirmation test used old function name `deserialize_order`**
- **Found during:** Task 2
- **Issue:** The confirmation test file called `deserialize_order(payload)` — the old agent-local name — which no longer exists
- **Fix:** Replaced all 3 call sites with `deserialize_proposed_order(payload)` to match the shared module's canonical name
- **Files modified:** agents/confirmation/tests/test_main.py

**3. [Rule 2 - Cleanup] monitoring test needed `portfolio_snapshot_to_dict`, `funding_payment_to_dict`, `fill_to_dict` at module level**
- **Found during:** Task 2
- **Issue:** The monitoring test had inline imports from reconciliation and execution agents; needed to consolidate
- **Fix:** Added all three functions to the module-level import from `libs.common.serialization`
- **Files modified:** agents/monitoring/tests/test_main.py

## Known Stubs

None. All functions are fully wired through `libs.common.serialization`.

## Threat Flags

None. This plan only changes import paths — function bodies are unchanged, no new trust boundaries introduced.

## Self-Check: PASSED

- agents/execution/main.py contains `from libs.common.serialization import`: FOUND
- agents/risk/main.py contains `from libs.common.serialization import`: FOUND
- agents/alpha/main.py contains `from libs.common.serialization import`: FOUND
- agents/signals/main.py contains `from libs.common.serialization import`: FOUND
- agents/signals/main.py still contains `def _json_safe`: FOUND
- agents/confirmation/main.py contains `from libs.common.serialization import`: FOUND
- agents/reconciliation/main.py contains `from libs.common.serialization import`: FOUND
- agents/reconciliation/paper_simulator.py contains `from libs.common.serialization import`: FOUND
- agents/monitoring/main.py contains `from libs.common.serialization import`: FOUND
- agents/monitoring/main.py still contains `def alert_to_dict` and `def deserialize_alert`: FOUND
- agents/ingestion/normalizer.py contains `from libs.common.serialization import snapshot_to_dict`: FOUND
- No agent-local deserialize_*/to_dict functions remain (except alert pair): VERIFIED
- No cross-agent test imports for serialization functions: VERIFIED
- Full suite: 1205 passed, 5 skipped: VERIFIED
- Commit fa5c839: FOUND
- Commit 59b103a: FOUND
