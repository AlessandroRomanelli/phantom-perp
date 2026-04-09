---
phase: 24-risk-engine-enhancements
plan: 01
subsystem: risk
tags: [risk-engine, correlation, exposure-check, tdd]
dependency_graph:
  requires: []
  provides: [correlation-exposure-check-5.5, risk-limits-correlation-fields]
  affects: [agents/risk/main.py, agents/risk/limits.py, configs/default.yaml]
tech_stack:
  added: []
  patterns: [correlation-group-check, signed-net-notional, TDD-red-green]
key_files:
  created: []
  modified:
    - agents/risk/limits.py
    - agents/risk/main.py
    - agents/risk/tests/test_limits.py
    - agents/risk/tests/test_risk_engine.py
    - configs/default.yaml
decisions:
  - "Signed net notional: LONG = +size*mark, SHORT = -size*mark — hedged positions offset in directional calculation"
  - "max_new_notional uses equity * max_position_pct_equity as conservative upper bound — avoids needing actual size pre-computation"
  - "check 5.5 inserted after check 6 (concurrent positions) and before check 7 (position sizing) — correlation check is O(groups*positions) and cheap"
  - "Module-level load_instruments in tests must include all instruments since load_instruments clears the registry on each call"
metrics:
  duration_minutes: 15
  completed_date: "2026-04-09"
  tasks_completed: 2
  files_modified: 5
---

# Phase 24 Plan 01: Correlation Exposure Check Summary

**One-liner:** Cross-instrument correlation exposure guard using signed net directional notional capped at configurable % of equity per route.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Add correlation fields to RiskLimits and YAML | 3134866 | limits.py, test_limits.py, default.yaml |
| 2 | Add correlation exposure check 5.5 to RiskEngine | e434a1b | main.py, test_risk_engine.py |

## What Was Built

**RiskLimits (Task 1):**
- Added `correlation_enabled: bool = True` field
- Added `max_net_directional_exposure_pct: Decimal = Decimal("100.0")` field
- `limits_for_route()` parses both fields from YAML route sections with safe defaults
- `configs/default.yaml` updated with `correlation_groups` (two groups: crypto and equity indices) and per-route config (Route A: 100%, Route B: 80%)

**RiskEngine check 5.5 (Task 2):**
- `RiskEngine.__init__` accepts optional `correlation_groups: list[list[str]] | None`
- Check 5.5 runs after check 6 (concurrent positions guard), before check 7 (position sizing):
  1. Skip if `correlation_enabled=False` or no groups configured
  2. Find the instrument's group (each instrument in at most one group)
  3. Skip if instrument not in any group
  4. Sum signed net notional of existing positions in the group (LONG = positive, SHORT = negative)
  5. Compute `max_new_notional = equity * max_position_pct_equity / 100` (conservative upper bound)
  6. `projected_net = abs(net_existing + direction_sign * max_new_notional)`
  7. Reject if `projected_net > equity * max_net_directional_exposure_pct / 100`
- `run_agent()` parses `correlation_groups` from `config["risk"]` and passes to `RiskEngine`

## Test Coverage

- **TestCorrelationLimits** (7 tests): field defaults, explicit True/False, limits_for_route parsing for A and B, missing keys use defaults
- **TestCorrelationExposure** (7 tests): rejection when over threshold, approval under threshold, SHORT offsets LONG, instrument not in group skips check, correlation_enabled=False skips check, empty groups skips check, no open positions approved
- **Total risk suite:** 112 tests, all passing (was 98 before this plan)

## Deviations from Plan

**1. [Rule 1 - Bug] load_instruments clears registry — test module-level registration must be comprehensive**
- **Found during:** Task 2 GREEN phase
- **Issue:** Second `load_instruments` call for BTC-PERP and SOL-PERP cleared ETH-PERP from registry, causing KeyError in subsequent tests
- **Fix:** Combined all instrument registrations (ETH, BTC, SOL, QQQ) into a single module-level call; removed inline `load_instruments` call inside `test_instrument_not_in_group_skips_check`
- **Files modified:** `agents/risk/tests/test_risk_engine.py`
- **Commit:** e434a1b

**2. [Rule 1 - Bug] test_instrument_not_in_group_skips_check used ETH-PERP idea (which is in the group)**
- **Found during:** Task 2 GREEN phase
- **Issue:** `_idea()` defaults to `TEST_INSTRUMENT_ID = "ETH-PERP"` which IS in the crypto correlation group, so the check correctly fired for it
- **Fix:** Replaced `_idea()` call with an inline `RankedTradeIdea` for QQQ-PERP (not in the crypto group)
- **Files modified:** `agents/risk/tests/test_risk_engine.py`
- **Commit:** e434a1b

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced.

## Known Stubs

None — all correlation data flows from live `PortfolioSnapshot.open_positions` and `RiskLimits` fields.

## Self-Check: PASSED

- `agents/risk/limits.py`: FOUND (contains `correlation_enabled`)
- `agents/risk/main.py`: FOUND (contains `Correlation exposure`)
- `agents/risk/tests/test_limits.py`: FOUND (contains `TestCorrelationLimits`)
- `agents/risk/tests/test_risk_engine.py`: FOUND (contains `TestCorrelationExposure`)
- `configs/default.yaml`: FOUND (contains `correlation_groups`)
- Commit 3134866: FOUND
- Commit e434a1b: FOUND
