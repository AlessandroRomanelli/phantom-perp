# Roadmap: Phantom Perp

## Milestones

- ✅ **v1.0 Strategy Enhancement** - Phases 1-5 (shipped 2026-03-22)
- ✅ **v1.1 Multi-Instrument Ingestion** - Phases 6-9.1 (shipped 2026-03-23)
- 🚧 **v1.2 AI-Powered Parameter Tuner** - Phases 10-15 (in progress)

## Phases

<details>
<summary>✅ v1.0 Strategy Enhancement (Phases 1-5) — SHIPPED 2026-03-22</summary>

See MILESTONES.md for v1.0 accomplishments.

</details>

<details>
<summary>✅ v1.1 Multi-Instrument Ingestion (Phases 6-9.1) — SHIPPED 2026-03-23</summary>

See MILESTONES.md for v1.1 accomplishments.

</details>

### 🚧 v1.2 AI-Powered Parameter Tuner (In Progress)

**Milestone Goal:** Add a daily tuner container that uses Claude to analyze Portfolio A's trading performance and intelligently adjust strategy + risk parameters — closing the gap between Portfolio A (-$204) and Portfolio B (+$120) by learning from trade history.

## Phase Details

### Phase 10: PostgreSQL Data Pipeline
**Goal**: Tuner can query Portfolio A fills with verified strategy attribution from PostgreSQL
**Depends on**: Phase 9.1 (v1.1 shipped)
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04
**Success Criteria** (what must be TRUE):
  1. Tuner queries Portfolio A fill records filtered by `portfolio_target=A` and returns typed results
  2. Every fill record carries a `signal_source` column identifying the originating strategy (column added and backfilled if absent)
  3. Tuner produces per-strategy performance data across all 5 instruments from a single query run
  4. Tuner produces per-instrument performance data across all strategies from the same data set
**Plans:** 2/2 plans complete
Plans:
- [x] 10-01-PLAN.md — ORM models, RelationalStore refactor, repository query layer, unit tests
- [x] 10-02-PLAN.md — Agent write injections (signals, risk, execution agents)

### Phase 11: Metrics Engine
**Goal**: Tuner computes expectancy-first performance metrics per (strategy, instrument) with minimum-count gates
**Depends on**: Phase 10
**Requirements**: METR-01, METR-02, METR-03, METR-04
**Success Criteria** (what must be TRUE):
  1. Tuner computes expectancy (avg_win × win_rate − avg_loss × loss_rate) as primary metric for each (strategy, instrument) pair
  2. Tuner computes profit factor (gross profit / gross loss) per strategy per instrument
  3. Tuner computes max drawdown amount and duration per strategy
  4. All P&L metrics are fee-adjusted (trading fees and funding costs subtracted)
  5. Strategy/instrument combinations below minimum trade count return null, not a computed percentage
**Plans**: TBD

### Phase 12: Safety & Bounds
**Goal**: Every tunable parameter has hard-coded bounds and YAML writes are atomic, validated, and audited — all safety guarantees proven before Claude produces any output
**Depends on**: Phase 11
**Requirements**: SAFE-01, SAFE-02, SAFE-03, SAFE-04
**Success Criteria** (what must be TRUE):
  1. A bounds registry YAML defines hard min/max for every tunable parameter, and applying any value outside bounds raises an error
  2. YAML config writes use atomic rename (`os.replace`) — no partial writes possible even if the process is killed mid-write
  3. After every write, the tuner re-parses the written YAML and confirms all values match intent; mismatches leave the original file untouched
  4. Every parameter change (or no-change) produces a structured log entry with before/after values, strategy, instrument, and timestamp
**Plans**: TBD

### Phase 13: Claude Integration
**Goal**: Tuner calls Claude API with performance metrics and bounds context, validates all recommendations before applying
**Depends on**: Phase 12
**Requirements**: CLAI-01, CLAI-02, CLAI-03, CLAI-04
**Success Criteria** (what must be TRUE):
  1. Tuner sends metrics summary to Claude via Anthropic SDK and receives a structured JSON response
  2. Claude response contains typed parameter recommendations with a per-parameter reasoning string
  3. Claude prompt includes current parameter values, hard bounds, and recent performance context for each recommendation slot
  4. Every Claude recommendation is clipped to hard bounds before being applied — no recommendation can bypass the bounds layer
**Plans**: TBD
**UI hint**: no

### Phase 14: Docker Infrastructure
**Goal**: Tuner runs as a self-contained run-to-completion container sharing a config volume with the signals agent, triggered daily by cron
**Depends on**: Phase 13
**Requirements**: INFR-01, INFR-02, INFR-03, INFR-04
**Success Criteria** (what must be TRUE):
  1. Tuner container starts, runs to completion, and exits with a zero code — it does not run as a daemon
  2. YAML files written by the tuner container are readable by the signals container via a shared Docker named volume
  3. A daily cron job triggers the tuner container and restarts the signals agent after completion
  4. Tuner Dockerfile builds successfully using the same Python 3.13-slim base and layer-caching pattern as existing agent images
**Plans**: TBD

### Phase 15: Telegram Notifications & End-to-End Acceptance
**Goal**: Operator receives a Telegram message each day with every parameter change and Claude's reasoning, and the full pipeline passes end-to-end acceptance
**Depends on**: Phase 14
**Requirements**: NOTF-01, NOTF-02, NOTF-03
**Success Criteria** (what must be TRUE):
  1. Operator receives a Telegram message after each tuning run listing every changed parameter with Claude's reasoning
  2. Telegram message shows before and after values for each changed parameter (or an explicit no-change message when nothing changed)
  3. Every tuning run produces a structured log entry regardless of whether changes were applied
  4. End-to-end pipeline — data → metrics → bounds → Claude → config write → Telegram — completes successfully against live PostgreSQL data
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 10 → 11 → 12 → 13 → 14 → 15

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 10. PostgreSQL Data Pipeline | v1.2 | 2/2 | Complete   | 2026-03-24 |
| 11. Metrics Engine | v1.2 | 0/? | Not started | - |
| 12. Safety & Bounds | v1.2 | 0/? | Not started | - |
| 13. Claude Integration | v1.2 | 0/? | Not started | - |
| 14. Docker Infrastructure | v1.2 | 0/? | Not started | - |
| 15. Telegram Notifications & End-to-End Acceptance | v1.2 | 0/? | Not started | - |
