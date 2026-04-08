# Roadmap: Phantom Perp

## Milestones

- ✅ **v1.0 Strategy Enhancement** - Phases 1-5 (shipped 2026-03-22)
- ✅ **v1.1 Multi-Instrument Ingestion** - Phases 6-9.1 (shipped 2026-03-23)
- 🚧 **v1.2 AI-Powered Parameter Tuner** - Phases 10-15 (in progress)
- ✅ **v1.3 Concerns Resolution** - Phases 16-20 (shipped 2026-04-08)

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

### 🚧 v1.3 Concerns Resolution (In Progress)

**Milestone Goal:** Fix critical bugs, security gaps, and tech debt identified in the codebase audit — hardening the system for reliable paper and live trading.

- [x] **Phase 16: Centralized Deserialization** - Build shared deserialization module in libs (completed 2026-04-08)
- [x] **Phase 17: Bug Fixes** - Apply all three bug fixes using centralized module (completed 2026-04-08)
- [x] **Phase 18: Messaging Infrastructure** - Add XAUTOCLAIM-based PEL cleanup for crashed consumers (completed 2026-04-08)
- [x] **Phase 19: Core Infrastructure Tests** - Unit tests for libs/messaging and libs/portfolio/router (completed 2026-04-08)
- [x] **Phase 20: Risk & Indicator Tests** - Unit tests for risk submodules and indicator modules (completed 2026-04-08)

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
**Plans**: 2 plans
Plans:
- [x] 11-01-PLAN.md — Round-trip reconstruction: VWAP aggregation, FIFO pairing, P&L computation (TDD)
- [x] 11-02-PLAN.md — Metrics computation: expectancy, profit factor, drawdown, min-count gate (TDD)

### Phase 12: Safety & Bounds
**Goal**: Every tunable parameter has hard-coded bounds and YAML writes are atomic, validated, and audited — all safety guarantees proven before Claude produces any output
**Depends on**: Phase 11
**Requirements**: SAFE-01, SAFE-02, SAFE-03, SAFE-04
**Success Criteria** (what must be TRUE):
  1. A bounds registry YAML defines hard min/max for every tunable parameter, and applying any value outside bounds raises an error
  2. YAML config writes use atomic rename (`os.replace`) — no partial writes possible even if the process is killed mid-write
  3. After every write, the tuner re-parses the written YAML and confirms all values match intent; mismatches leave the original file untouched
  4. Every parameter change (or no-change) produces a structured log entry with before/after values, strategy, instrument, and timestamp
**Plans**: 2 plans
Plans:
- [x] 12-01-PLAN.md — Bounds registry + audit logging: bounds.yaml, BoundsEntry, validate_value, ParameterChange, structlog wrappers (TDD)
- [x] 12-02-PLAN.md — Atomic YAML writer: apply_parameter_changes with os.replace, post-write validation, rollback, Schema A/B handling (TDD)

### Phase 13: Claude Integration
**Goal**: Tuner calls Claude API with performance metrics and bounds context, validates all recommendations before applying
**Depends on**: Phase 12
**Requirements**: CLAI-01, CLAI-02, CLAI-03, CLAI-04
**Success Criteria** (what must be TRUE):
  1. Tuner sends metrics summary to Claude via Anthropic SDK and receives a structured JSON response
  2. Claude response contains typed parameter recommendations with a per-parameter reasoning string
  3. Claude prompt includes current parameter values, hard bounds, and recent performance context for each recommendation slot
  4. Every Claude recommendation is clipped to hard bounds before being applied — no recommendation can bypass the bounds layer
**Plans**: 2 plans
Plans:
- [x] 13-01-PLAN.md — Claude client: prompt builder, Anthropic SDK caller with forced tool use, response parser (TDD)
- [ ] 13-02-PLAN.md — Recommender: validation pipeline (clip/reject/coerce), tuning cycle orchestrator, audit logging (TDD)
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
**Plans**: 2 plans
Plans:
- [x] 14-01-PLAN.md — Tuner entrypoint (bootstrap, DB fetch, exit codes), Dockerfile (python:3.13-slim, no TA-Lib), pyproject.toml tuner dep group
- [ ] 14-02-PLAN.md — Scheduler container (alpine + crond + docker-cli), compose integration (tuner + scheduler + strategy_configs volume)

### Phase 15: Telegram Notifications & End-to-End Acceptance
**Goal**: Operator receives a Telegram message each day with every parameter change and Claude's reasoning, and the full pipeline passes end-to-end acceptance
**Depends on**: Phase 14
**Requirements**: NOTF-01, NOTF-02, NOTF-03
**Success Criteria** (what must be TRUE):
  1. Operator receives a Telegram message after each tuning run listing every changed parameter with Claude's reasoning
  2. Telegram message shows before and after values for each changed parameter (or an explicit no-change message when nothing changed)
  3. Every tuning run produces a structured log entry regardless of whether changes were applied
  4. End-to-end pipeline — data → metrics → bounds → Claude → config write → Telegram — completes successfully against live PostgreSQL data
**Plans**: 2 plans
Plans:
- [ ] 15-01-PLAN.md — Telegram notification module: report formatter, TunerNotifier, entrypoint hook (TDD)
- [ ] 15-02-PLAN.md — E2E smoke test, milestone documentation wrap-up
**UI hint**: no

### Phase 16: Centralized Deserialization
**Goal**: All agent deserialization logic is consolidated into a single shared module in libs, eliminating copy-paste drift across 5 agents
**Depends on**: Phase 15 (v1.2 milestone; v1.3 work is independent and can start in parallel)
**Requirements**: BUG-04
**Success Criteria** (what must be TRUE):
  1. A single `libs/common/serialization.py` module (or equivalent) provides `deserialize_position`, `deserialize_order`, and related helpers used by all agents
  2. Every agent that previously had local deserialization logic imports from the shared module — no agent-local deserialization copies remain
  3. The shared module's `reduce_only` parsing accepts "true", "True", "1", True and any other encountered string representation without raising
  4. Existing agent behavior is unchanged after the refactor — all existing deserialization tests continue to pass
**Plans:** 2/2 plans complete
Plans:
- [x] 16-01-PLAN.md — Centralized serialization module with TDD test suite (libs/common/serialization.py)
- [x] 16-02-PLAN.md — Migrate all agents to shared module, update test imports

### Phase 17: Bug Fixes
**Goal**: Paper mode risk guardrails see real positions, dedup eviction is FIFO-ordered, and reduce_only parsing is correct across all agents
**Depends on**: Phase 16
**Requirements**: BUG-01, BUG-02, BUG-03
**Success Criteria** (what must be TRUE):
  1. In paper mode, the risk agent evaluates `max_position` and same-instrument stacking guards against the actual open positions list, not an empty list
  2. The execution agent's dedup set evicts the oldest order ID when the set exceeds capacity — the first entry added is always the first removed
  3. A `reduce_only` field set to "true", "True", "1", or True deserializes to Python `True` in all agents (risk, reconciliation, monitoring, execution)
  4. No regression in live mode — all existing risk guardrail tests pass unchanged
**Plans:** 2/2 plans complete
Plans:
- [x] 17-01-PLAN.md — Paper mode position deserialization fix (BUG-01) + BUG-03 verification
- [x] 17-02-PLAN.md — FIFO dedup eviction fix (BUG-02)

### Phase 18: Messaging Infrastructure
**Goal**: Crashed consumer agents automatically recover their pending messages without manual intervention
**Depends on**: Phase 16
**Requirements**: INFR-01
**Success Criteria** (what must be TRUE):
  1. Any message idle in the Pending Entry List for longer than a configurable timeout is automatically reclaimed and redelivered to an active consumer
  2. The reclaim loop runs as a background task within each consumer agent and does not block message processing
  3. The idle timeout and batch size for reclaim are configurable per-agent via YAML or environment variable
  4. A structured log entry is emitted each time a message is reclaimed, identifying the original consumer ID and message ID
**Plans:** 1/1 plans complete
Plans:
- [x] 18-01-PLAN.md — PEL reclaim via XAUTOCLAIM background loop in RedisConsumer (TDD)

### Phase 19: Core Infrastructure Tests
**Goal**: libs/messaging and libs/portfolio/router have complete unit test suites covering all routing rules and message lifecycle paths
**Depends on**: Phase 18
**Requirements**: TEST-01, TEST-02
**Success Criteria** (what must be TRUE):
  1. RedisPublisher tests cover publish success, publish failure, and connection error paths using fakeredis
  2. RedisConsumer tests cover consume, acknowledge, and error paths — including the XAUTOCLAIM reclaim path added in Phase 18
  3. Portfolio router tests verify every routing rule: time horizon short → Route A, high-frequency source → Route A, low conviction → Route B, default fallback
  4. Router tests cover all 5 instruments and both routes — no routing combination is left untested
**Plans:** 2/2 plans complete
Plans:
- [x] 19-01-PLAN.md — Messaging tests (RedisPublisher, RedisConsumer, Channel)
- [x] 19-02-PLAN.md — Router tests (RouteRouter routing rules)

### Phase 20: Risk & Indicator Tests
**Goal**: Risk submodules and all indicator modules have unit tests that verify correctness against known inputs and boundary conditions
**Depends on**: Phase 19
**Requirements**: TEST-03, TEST-04
**Success Criteria** (what must be TRUE):
  1. margin_calculator tests cover initial margin, maintenance margin, and liquidation price calculation for both Route A and Route B leverage limits
  2. liquidation_guard tests verify the minimum liquidation distance check rejects positions too close to liquidation and passes those safely distant
  3. position_sizer tests verify sizing scales correctly with equity and risk budget, and never exceeds max_position_pct_equity
  4. All indicator modules (each in `libs/indicators/`) have at least one test verifying output against a hand-calculated known input
  5. Indicator boundary tests cover edge cases: empty series, single-element series, and all-identical values
**Plans:** 2/2 plans complete
Plans:
- [x] 20-01-PLAN.md — Risk submodule tests (Route A/B margin, liquidation, equity bounds)
- [x] 20-02-PLAN.md — Indicator known-value and boundary tests

## Progress

**Execution Order:**
Phases execute in numeric order: 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 10. PostgreSQL Data Pipeline | v1.2 | 2/2 | Complete    | 2026-03-29 |
| 11. Metrics Engine | v1.2 | 2/2 | Complete    | 2026-03-25 |
| 12. Safety & Bounds | v1.2 | 2/2 | Complete    | 2026-03-25 |
| 13. Claude Integration | v1.2 | 1/2 | Complete    | 2026-03-25 |
| 14. Docker Infrastructure | v1.2 | 1/2 | Complete    | 2026-03-25 |
| 15. Telegram Notifications & End-to-End Acceptance | v1.2 | 0/2 | Not started | - |
| 16. Centralized Deserialization | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 17. Bug Fixes | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 18. Messaging Infrastructure | v1.3 | 1/1 | Complete    | 2026-04-08 |
| 19. Core Infrastructure Tests | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 20. Risk & Indicator Tests | v1.3 | 2/2 | Complete    | 2026-04-08 |
