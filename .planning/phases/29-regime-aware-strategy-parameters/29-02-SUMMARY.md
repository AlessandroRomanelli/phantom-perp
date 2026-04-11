---
phase: 29-regime-aware-strategy-parameters
plan: "02"
subsystem: signals-agent
tags: [regime, strategy-params, override-chain, main-loop]
dependency_graph:
  requires:
    - 29-01
  provides:
    - regime-aware-strategy-evaluation
  affects:
    - agents/signals/main.py
tech_stack:
  added: []
  patterns:
    - "Regime override merge: {**session_overrides, **regime_overrides, **orch_adj}"
    - "replace(snapshot, regime=current_regime) before strategy loop"
    - "prev_regimes dict for transition logging"
key_files:
  created:
    - tests/unit/test_regime_overrides.py
  modified:
    - agents/signals/main.py
decisions:
  - "Regime config loaded at startup in run_agent() alongside session_config — single load, no runtime reload"
  - "prev_regimes initialized as dict[str, MarketRegime | None] to correctly handle first-transition logging (from_regime=none)"
  - "snapshot = replace(snapshot, regime=current_regime) placed after regime_detector.update() and before strategy loop — all strategies in the instrument loop receive the updated snapshot"
metrics:
  duration: "~10 minutes"
  completed: "2026-04-11"
  tasks_completed: 2
  files_changed: 2
---

# Phase 29 Plan 02: Regime Wiring into Signals Main Loop Summary

**One-liner:** Regime detection wired into the signals agent main loop with correct override priority chain (session < regime < orchestrator) and regime transition logging.

## What Was Built

Plan 02 completed the regime-aware parameter system by connecting the data model and config layer (Plan 01) to runtime strategy evaluation in `agents/signals/main.py`.

Three surgical changes were made to `agents/signals/main.py`:

1. **Startup: regime config loaded** — `regime_config = load_regime_config()` added immediately after `session_config = load_session_config()` in `run_agent()`. Logs `regime_config_loaded` with strategy names at INFO.

2. **Per-snapshot: regime attached** — After `regime_detector.update(snapshot)`, the current regime is read via `regime_detector.regime_for(instrument)` and attached to the snapshot via `replace(snapshot, regime=current_regime)`. Regime transitions are logged at INFO with `instrument`, `from_regime`, and `to_regime` fields. A `prev_regimes: dict[str, MarketRegime | None] = {}` dict tracks per-instrument previous regime.

3. **Per-strategy: override chain updated** — The override merge block was updated to insert regime overrides between session overrides and orchestrator adjustments:
   ```python
   session_overrides = get_session_overrides(...)
   regime_overrides = get_regime_overrides(regime_config, strategy.name, snapshot.regime)
   overrides = {**session_overrides, **regime_overrides}
   orch_adj = orchestrator_param_adj.get(...)
   if orch_adj:
       overrides = {**overrides, **orch_adj}
   originals = _apply_session_overrides(strategy, overrides)
   ```

## Tests Created

`tests/unit/test_regime_overrides.py` — 8 tests covering:
- Session override alone applies and restores
- Regime override alone applies and restores
- Regime wins over session on conflict (D-02)
- Non-conflicting session + regime both apply
- Orchestrator wins over regime (ORCH-11)
- Full priority chain: session=0.30, regime=0.28, orch=0.20 → final 0.20
- Apply + restore full chain returns original base values
- Unknown param keys silently ignored (T-29-05 security)

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The changes are purely in-process data flow within the signals agent main loop.

## Known Stubs

None — all regime wiring is fully connected. `regime_detector.regime_for(instrument)` returns a real `MarketRegime` enum value populated by `RegimeDetector.update()` which was already wired in Plan 01's prior wave work.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| tests/unit/test_regime_overrides.py | FOUND |
| agents/signals/main.py | FOUND |
| 29-02-SUMMARY.md | FOUND |
| commit f90d529 (test task 1) | FOUND |
| commit 4f6c2ee (feat task 2) | FOUND |
