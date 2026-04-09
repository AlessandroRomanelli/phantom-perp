---
phase: 24-risk-engine-enhancements
plan: 02
subsystem: risk
tags: [risk-engine, drawdown, hwm, kill-switch, safety]
requirements: [ROBU-04]

dependency_graph:
  requires: [24-01]
  provides: [true-hwm-drawdown-check]
  affects: [agents/risk/main.py, agents/risk/limits.py]

tech_stack:
  added: []
  patterns: [high-water-mark tracking per-route, TDD red-green cycle]

key_files:
  created: []
  modified:
    - agents/risk/limits.py
    - agents/risk/main.py
    - agents/risk/tests/test_limits.py
    - agents/risk/tests/test_risk_engine.py
    - configs/default.yaml

decisions:
  - "HWM initialized to Decimal('0') per route; first observed equity becomes the initial peak"
  - "hwm_drawdown_enabled=True by default — operator must explicitly disable via YAML"
  - "Rejection message includes HWM and current equity values for observability"
  - "Old daily_loss_pct proxy removed entirely from check 5; check 4 (daily loss) unchanged"

metrics:
  duration: ~10min
  completed: 2026-04-09
  tasks_completed: 2
  files_modified: 5
---

# Phase 24 Plan 02: HWM Drawdown Tracking Summary

Replace the drawdown proxy (daily P&L reused as drawdown stand-in) with true per-route high-water mark drawdown tracking in RiskEngine (ROBU-04).

## What Was Built

**Task 1 — `hwm_drawdown_enabled` field in RiskLimits**

Added `hwm_drawdown_enabled: bool = True` to the `RiskLimits` frozen dataclass after `max_net_directional_exposure_pct`. Updated `limits_for_route()` to parse the flag from YAML with `section.get("hwm_drawdown_enabled", True)`, consistent with the `stop_loss_required` and `correlation_enabled` parsing pattern. Added `hwm_drawdown_enabled: true` under both `route_a` and `route_b` in `configs/default.yaml`.

Tests added (`TestHWMLimits`, 6 tests): enabled/disabled construction, default=True, limits_for_route round-trip for both routes, missing-key default.

**Task 2 — True HWM drawdown check in RiskEngine**

Added `self._hwm: dict[Route, Decimal] = {Route.A: Decimal("0"), Route.B: Decimal("0")}` to `RiskEngine.__init__`.

Replaced check 5 (the old `drawdown_pct = daily_loss_pct` proxy block) with:
- Update `_hwm[target]` if current equity exceeds the stored peak
- Compute `true_drawdown_pct = (hwm - equity) / hwm * 100`
- Reject if `true_drawdown_pct > limits.max_drawdown_pct`
- Skip entirely when `limits.hwm_drawdown_enabled is False`
- Rejection message includes `HWM={hwm:.2f}, current={equity:.2f}` for observability

Check 4 (daily loss kill switch) remains completely unchanged.

Tests added (`TestHWMDrawdown`, 7 tests): first-call no rejection, rise→small-drop approved, rise→large-drop rejected (29.2% > 25%), disabled skips check, per-route isolation, HWM never decreases, daily loss check still fires independently.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

None beyond what was in the plan's threat model (T-24-04, T-24-05, T-24-06 all accepted).

## Self-Check: PASSED

- `agents/risk/limits.py` — modified, hwm_drawdown_enabled field present
- `agents/risk/main.py` — modified, _hwm dict and HWM check present
- `agents/risk/tests/test_limits.py` — modified, TestHWMLimits present
- `agents/risk/tests/test_risk_engine.py` — modified, TestHWMDrawdown present
- `configs/default.yaml` — modified, hwm_drawdown_enabled: true in both routes
- Commits: 63dfe65 (Task 1), add8874 (Task 2)
- Full test suite: 125 passed, 0 failed
