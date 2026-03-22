# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Strategy Enhancement

**Shipped:** 2026-03-22
**Phases:** 5 | **Plans:** 14 | **Sessions:** 1

### What Was Built
- 7 trading strategies (5 improved + 2 new) with per-instrument tuning across 5 perpetual contracts
- Shared utility layer: funding filter, adaptive conviction, swing points, session classifier, conviction normalizer
- Session-aware parameter selection with separate configs for crypto weekends and equity off-hours
- Unified Portfolio A routing via conviction normalization at 0.70 threshold
- Config infrastructure: strategy matrix, schema validation, startup diff logging

### What Worked
- Phase 2 patterns (volume confirmation, adaptive conviction, Portfolio A routing) reused directly in Phases 3-4 — high pattern leverage
- Per-instrument tuning in Phase 1 before strategy logic changes — established the config structure early
- Parallel plan execution within waves — each strategy is a vertical slice with no file overlap
- TDD approach: writing tests first caught regressions immediately
- Phase 5 utility extraction was mechanical — all patterns were inline in Phases 2-4

### What Was Inefficient
- Phase 1 INFRA-01 (per-instrument cooldown) turned out to already be satisfied — could have been caught earlier with a codebase scout
- VWAP feasibility was flagged as uncertain but passed easily — the concern was overweighted
- Some parallel executor agents introduced minor issues (ACTIVE_INSTRUMENT_IDS missing constant) that required post-wave fixes

### Patterns Established
- Shared utility pattern: function-based modules returning frozen dataclass results (funding_filter.py → adaptive_conviction.py → swing_points.py)
- Per-instrument config merging with schema validation and diff logging
- Strategy matrix as single source of truth for strategy-instrument enablement
- Session config in separate file, applied at evaluate()-time with save/restore pattern
- Conviction normalization as post-processing overlay (never mutates raw conviction)

### Key Lessons
1. Extracting inline implementations to shared utilities (Phase 5) is trivial when the inline code was written with consistent patterns — worth investing in pattern consistency early
2. Volume data (bar_volumes) turned out to be useful across many strategies — the Phase 1 investment in FeatureStore extensions paid compound returns
3. Parallel execution works well when plans have zero file overlap — the wave/dependency system catches conflicts

### Cost Observations
- Model mix: ~70% opus (executors, researchers, planners), ~30% sonnet (checkers, verifiers)
- Sessions: 1 continuous session
- Notable: 14 plans executed across 5 phases in a single session — high throughput from parallel wave execution

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 1 | 5 | Established GSD workflow with discuss→plan→execute→verify cycle |

### Cumulative Quality

| Milestone | Tests | Zero-Dep Additions |
|-----------|-------|--------------------|
| v1.0 | 736 | 2 (scipy, bottleneck — both already transitive deps) |

### Top Lessons (Verified Across Milestones)

1. Pattern consistency in early phases enables mechanical extraction in later phases
2. Per-instrument config infrastructure should precede strategy logic changes
