---
phase: 21-safety-critical-fixes
plan: 02
subsystem: risk, reconciliation, config
tags: [safety, regression-test, credentials, dual-portfolio]
dependency_graph:
  requires: []
  provides: [SAFE-02-kill-switch-tests, SAFE-05-route-b-credentials]
  affects: [agents/risk, agents/reconciliation, libs/common/config]
tech_stack:
  added: []
  patterns: [dual-client-credential-routing, _create_live_clients helper]
key_files:
  created: []
  modified:
    - agents/risk/tests/test_risk_engine.py
    - libs/common/config.py
    - libs/common/tests/test_config_validation.py
    - agents/reconciliation/main.py
    - agents/reconciliation/tests/test_main.py
decisions:
  - "_create_live_clients() helper extracted for testability — allows unit tests without running run_agent() loop"
  - "client_b is not client_a identity check in finally block — safe dual-close without double-close"
  - "api_key_b empty string means fallback — no None handling needed, empty str is falsy"
metrics:
  duration: ~5m
  completed: "2026-04-08T22:41:00Z"
  tasks_completed: 1
  files_changed: 5
---

# Phase 21 Plan 02: Kill Switch Regression Tests + Route B Dual Credentials Summary

**One-liner:** Kill switch regression tests with real Decimal("-600") P&L + optional Route B API key/secret fields with dual-client reconciliation fallback.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Kill switch regression test (SAFE-02) + Route B credential support (SAFE-05) | d6fb4e5 | test_risk_engine.py, config.py, test_config_validation.py, reconciliation/main.py, reconciliation/tests/test_main.py |

## What Was Built

### SAFE-02: Kill Switch Regression Tests

Added two new test methods to `TestDailyLossKillSwitch` in `agents/risk/tests/test_risk_engine.py`:

- **`test_daily_loss_kill_switch_fires_on_real_loss`**: Creates `PortfolioSnapshot` with `realized_pnl_today_usdc=Decimal("-600")` on `equity=Decimal("5000")` → 12% > 10% limit → asserts `approved is False` and `"Daily loss kill switch"` in reason
- **`test_daily_loss_kill_switch_allows_small_loss`**: Creates snapshot with `realized_pnl_today_usdc=Decimal("-100")` on `equity=Decimal("5000")` → 2% < 10% limit → asserts no "Daily loss" rejection

These prove the kill switch fires on real non-zero P&L values, preventing future regressions where hardcoded zeros could mask losses.

### SAFE-05: Route B Dual Credentials

**`libs/common/config.py`** — Added optional fields to `CoinbaseSettings`:
```python
api_key_b: str = ""       # Optional — uses api_key_a as fallback if empty
api_secret_b: str = ""    # Optional — uses api_secret_a as fallback if empty
```
Both read from `COINBASE_ADV_API_KEY_B` / `COINBASE_ADV_API_SECRET_B` env vars.

**`agents/reconciliation/main.py`** — Extracted `_create_live_clients(settings)` helper:
- When `api_key_b` is non-empty: creates separate `CoinbaseAuth` + `CoinbaseRESTClient` for Route B, logs `route_b_credentials_loaded`
- When `api_key_b` is empty: `client_b = client_a` (same object), logs `route_b_credentials_fallback`
- `run_agent()` uses `client_a` for Route A, `client_b` for Route B
- `finally` block: `await client_a.close(); if client_b is not client_a: await client_b.close()`

**`libs/common/tests/test_config_validation.py`** — Updated:
- `test_b_credential_fields_removed` → `test_b_credential_fields_are_optional`: asserts fields exist with `""` default
- Added `test_coinbase_settings_reads_api_key_b_from_env`: verifies env var binding

**`agents/reconciliation/tests/test_main.py`** — Added `TestRouteBCredentialRouting`:
- `test_route_b_uses_separate_credentials_when_set`: mocks settings with `api_key_b="key_b"`, verifies 2 `CoinbaseAuth` instances, different client objects
- `test_route_b_falls_back_to_route_a_credentials`: mocks settings with `api_key_b=""`, verifies 1 `CoinbaseAuth` instance, same client object

## Deviations from Plan

### Auto-added Functionality

**1. [Rule 2 - Missing critical functionality] Extracted _create_live_clients() helper**
- **Found during:** Task 1 implementation
- **Issue:** Plan specified credential routing logic inside `run_agent()`, but `run_agent()` runs indefinitely making it untestable
- **Fix:** Extracted `_create_live_clients(settings)` as a standalone synchronous function that can be imported and unit-tested directly
- **Files modified:** `agents/reconciliation/main.py`
- **Commit:** d6fb4e5

## Test Results

```
61 passed in 0.31s
(agents/risk/tests/test_risk_engine.py, agents/reconciliation/tests/test_main.py, libs/common/tests/test_config_validation.py)
```

Full suite: 276 passed, 1 pre-existing failure (`tests/unit/test_tuner_entrypoint.py::test_fetch_fills_calls_repository_correctly` — parameter name mismatch in tuner, unrelated to this plan).

## Known Stubs

None.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes at trust boundaries introduced. `api_key_b`/`api_secret_b` follow the existing `api_key_a`/`api_secret_a` pattern and are never logged.

## Self-Check: PASSED

- [x] `agents/risk/tests/test_risk_engine.py` contains `test_daily_loss_kill_switch_fires_on_real_loss` ✓
- [x] `agents/risk/tests/test_risk_engine.py` contains `test_daily_loss_kill_switch_allows_small_loss` ✓
- [x] `libs/common/config.py` `CoinbaseSettings` contains `api_key_b: str = ""` ✓
- [x] `libs/common/config.py` `CoinbaseSettings` contains `api_secret_b: str = ""` ✓
- [x] `agents/reconciliation/main.py` contains `_create_live_clients` ✓
- [x] `agents/reconciliation/main.py` contains `route_b_credentials_loaded` log ✓
- [x] `agents/reconciliation/main.py` contains `route_b_credentials_fallback` log ✓
- [x] `agents/reconciliation/tests/test_main.py` contains `test_route_b_uses_separate_credentials_when_set` ✓
- [x] `agents/reconciliation/tests/test_main.py` contains `test_route_b_falls_back_to_route_a_credentials` ✓
- [x] Commit d6fb4e5 exists ✓
