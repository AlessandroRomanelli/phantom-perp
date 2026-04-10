---
gsd_state_version: 1.0
milestone: v1.4
milestone_name: Forensic Audit Fixes
status: roadmap_created
stopped_at: v1.4 roadmap created — ready to plan Phase 21
last_updated: "2026-04-08T23:00:00.000Z"
last_activity: 2026-04-08
progress:
  total_phases: 5
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-08)

**Core value:** Fix structural profitability issues — eliminate bugs, recalibrate sizing/execution, fix corrupted data
**Current focus:** v1.4 Forensic Audit Fixes — Phase 21 (Safety Critical Fixes) next

## Current Position

Phase: 21 — Safety Critical Fixes (not started)
Plan: —
Status: Roadmap created, ready for phase planning
Last activity: 2026-04-10 - Completed quick task 260410-dcp: Fix position sizing bottlenecks

```
Progress: [                    ] 0/5 phases
```

## Performance Metrics

**Velocity:**

- Total plans completed: 4 (v1.3)
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 16. Centralized Deserialization | 2 | - | - |
| 17. Bug Fixes | 2 | - | - |
| 18. Messaging Infrastructure | 1 | - | - |
| 19. Core Infrastructure Tests | 2 | - | - |
| 20. Risk & Indicator Tests | 2 | - | - |

*Updated after each plan completion*

## Accumulated Context

### Decisions

- v1.2: Claude advises, code enforces bounds — AI reasons, deterministic layer applies
- v1.2: YAML rewrite over Redis overrides — auditable, git-diffable parameter changes
- v1.2: Daily tuning cadence — avoids overfitting from hourly micro-adjustments
- v1.2: Tuner is run-to-completion container, not daemon — triggered by external cron
- [Phase 10]: Store enum .value strings in DB columns to match existing serializer convention
- [Phase 10]: INNER JOIN in get_fills_by_strategy() — only attributed fills are relevant for per-strategy P&L
- [Phase 10]: init_db() uses engine.begin() not engine.connect() to auto-commit DDL
- [Phase 10]: DB writes are fire-and-forget (try/except guarded) — SQLAlchemyError caught before generic Exception per CLAUDE.md convention
- [Phase 10]: FillRecord written for both paper and live modes — analytics captures all fills regardless of execution mode
- [Phase 11-metrics-engine]: FIFO deque uses appendleft/pop for correct oldest-first pairing in overlapping entry scenarios
- [Phase 11-metrics-engine]: build_round_trips excludes keys with no closed round-trips to avoid empty list entries in output
- [Phase 11]: Zero-P&L round-trips classified as losses (conservative per net_pnl <= 0 -- fees make true breakeven negative)
- [Phase 11]: funding_costs_usdc = Decimal(0) placeholder per D-08 -- position lifecycle not available in Phase 10, deferred to METR-05/06
- [Phase 12-safety-bounds]: audit.py implemented fully in Task 1 alongside bounds.py -- avoids __init__.py ImportError that would block TDD GREEN phase for bounds tests
- [Phase 12-safety-bounds]: Backup via deepcopy not yaml round-trip -- keeps safe_load call count predictable for post-write validation
- [Phase 12-safety-bounds]: Rollback restores exact original bytes via read_bytes() -- preserves file formatting and comments
- [Phase 13-claude-integration]: DEFAULT_MODEL='claude-sonnet-4-5' stable alias -- avoids invalid 20250514 snapshot ID that never existed
- [Phase 13-claude-integration]: Forced tool_choice pattern guarantees structured response -- eliminates text-fallback branch in call_claude
- [Phase 13-claude-integration]: log_no_change called with strategy='all' for empty-rec runs -- single entry covers entire no-change run
- [Phase 13-claude-integration]: run_tuning_cycle loads strategy configs only for strategies appearing in metrics dict to avoid unnecessary YAML reads
- [Phase 14]: pyproject.toml tuner optional dep group excludes ta-lib to keep image lightweight
- [Phase 14]: asyncio.run() bridges sync entrypoint to async TunerRepository
- [Phase 14]: alpine:3.19 with docker-cli-compose for scheduler -- minimal image, compose v2 plugin for docker compose command
- [Phase 14]: strategy_configs named volume scope is configs/strategies/ only -- minimal surface area per D-02
- [Phase 14]: TUNER_CRON env var with sed substitution -- configurable daily default 00:00 UTC per D-05
- v1.3: BUG-04 (centralize deserialization) precedes BUG-01/BUG-03 — fixes are implemented in the centralized module, not patched in-place per agent
- v1.3: Phase 18 (INFR-01 PEL cleanup) precedes Phase 19 (TEST-01 messaging tests) — TEST-01 must cover the XAUTOCLAIM reclaim path added in Phase 18
- v1.4: Tier 1 safety fixes (Phase 21) precede all other work — trustworthy paper results are prerequisite for measuring profitability improvements
- v1.4: Data pipeline fixes (Phase 22) precede sizing optimization (Phase 23) — correct indicator values needed before recalibrating position sizing
- v1.4: Phase 24 (risk engine) depends only on Phase 21, not Phase 22/23 — can be parallelized with data/sizing work if needed

### Blockers/Concerns

None yet.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260410-dcp | Fix position sizing bottlenecks: concurrent positions, instrument stacking, regime leverage, notional cap, fee filter | 2026-04-10 | 897c1de | [260410-dcp-fix-position-sizing-bottlenecks-concurre](./quick/260410-dcp-fix-position-sizing-bottlenecks-concurre/) |

### Pending Todos

None yet.

## Session Continuity

Last session: 2026-04-08T23:00:00.000Z
Stopped at: v1.4 roadmap created — ready to plan Phase 21
Resume file: None
