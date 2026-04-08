# Roadmap: Phantom Perp

## Milestones

- ✅ **v1.0 Strategy Enhancement** - Phases 1-5 (shipped 2026-03-22)
- ✅ **v1.1 Multi-Instrument Ingestion** - Phases 6-9.1 (shipped 2026-03-23)
- 🚧 **v1.2 AI-Powered Parameter Tuner** - Phases 10-15 (in progress)
- ✅ **v1.3 Concerns Resolution** - Phases 16-20 (shipped 2026-04-08)
- 🚧 **v1.4 Forensic Audit Fixes** - Phases 21-25

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

### ✅ v1.3 Concerns Resolution (Shipped 2026-04-08)

**Milestone Goal:** Fix critical bugs, security gaps, and tech debt identified in the codebase audit — hardening the system for reliable paper and live trading.

- [x] **Phase 16: Centralized Deserialization** - Build shared deserialization module in libs (completed 2026-04-08)
- [x] **Phase 17: Bug Fixes** - Apply all three bug fixes using centralized module (completed 2026-04-08)
- [x] **Phase 18: Messaging Infrastructure** - Add XAUTOCLAIM-based PEL cleanup for crashed consumers (completed 2026-04-08)
- [x] **Phase 19: Core Infrastructure Tests** - Unit tests for libs/messaging and libs/portfolio/router (completed 2026-04-08)
- [x] **Phase 20: Risk & Indicator Tests** - Unit tests for risk submodules and indicator modules (completed 2026-04-08)

### 🚧 v1.4 Forensic Audit Fixes

**Milestone Goal:** Fix structural profitability issues identified by the 5-agent forensic audit — eliminate bugs causing financial loss, recalibrate sizing/execution to overcome fee drag, and fix corrupted data inputs degrading signal quality.

- [x] **Phase 21: Safety Critical Fixes** - Eliminate double execution, wire real P&L into kill switches, restore safety constants, fix credential routing (completed 2026-04-08)
- [ ] **Phase 22: Data Pipeline Fixes** - Fix corrupted bar_volumes, ADX NaN bug, Bollinger ddof, and index price sourcing
- [ ] **Phase 23: Sizing & Execution Optimization** - Recalibrate conviction sizing, switch SL to maker fees, add fee filter, cap BTC notional
- [ ] **Phase 24: Risk Engine Enhancements** - Add cross-instrument correlation check and equity HWM drawdown tracking
- [ ] **Phase 25: Paper Simulator Fidelity** - Probabilistic fill model with adverse selection and SL slippage

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
  1. Tuner computes expectancy (avg_win x win_rate - avg_loss x loss_rate) as primary metric for each (strategy, instrument) pair
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
  4. End-to-end pipeline — data -> metrics -> bounds -> Claude -> config write -> Telegram — completes successfully against live PostgreSQL data
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
  3. Portfolio router tests verify every routing rule: time horizon short -> Route A, high-frequency source -> Route A, low conviction -> Route B, default fallback
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

### Phase 21: Safety Critical Fixes
**Goal**: The system cannot lose money through known infrastructure bugs — double execution is impossible, kill switches use real P&L, metadata survives serialization, leverage constant matches spec, and Route B uses correct credentials
**Depends on**: Phase 20 (v1.3 shipped)
**Requirements**: SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05
**Success Criteria** (what must be TRUE):
  1. In paper mode, an order published to the execution stream is executed exactly once — the PaperBroker and paper_simulator do not both act on the same order
  2. The risk agent's daily loss kill switch fires when cumulative realized P&L (from reconciliation) exceeds the threshold — not when a hardcoded zero is compared
  3. A RankedTradeIdea serialized to Redis and deserialized back retains its funding_rate field with the original value, enabling the funding rate circuit breaker to evaluate correctly
  4. `MAX_LEVERAGE_GLOBAL` in `libs/common/constants.py` equals `Decimal("5.0")` and all leverage checks reference this constant
  5. The reconciliation agent's Route B portfolio poll authenticates with Route B API credentials (KEY_B, SECRET_B, PASSPHRASE_B), not Route A credentials
**Plans**: 2 plans
Plans:
- [x] 21-01-PLAN.md — Fix double execution (SAFE-01), metadata serialization (SAFE-03), leverage constant (SAFE-04)
- [x] 21-02-PLAN.md — Kill switch regression test (SAFE-02), Route B credentials (SAFE-05)

### Phase 22: Data Pipeline Fixes
**Goal**: Signal strategies receive correct indicator values and market data — bar_volumes are true per-bar deltas, ADX produces valid numbers, Bollinger Bands use correct statistics, and index_price is reliably sourced
**Depends on**: Phase 21
**Requirements**: PROF-03, PROF-04, ROBU-02, ROBU-05
**Success Criteria** (what must be TRUE):
  1. FeatureStore `bar_volumes` returns the volume traded within each candle bar (not the difference between consecutive 24h cumulative values) and the VWAP strategy produces correct volume-weighted prices
  2. The ADX indicator never returns NaN for a valid input series of sufficient length — `np.isnan()` is used for all NaN comparisons in the indicator pipeline
  3. Bollinger Bands use `ddof=1` (sample standard deviation) consistently with all other volatility calculations in the codebase
  4. MarketSnapshot `index_price` is populated from exchange data when available, and strategies that depend on basis (index vs mark) gracefully degrade or skip when index_price is unavailable
**Plans**: 2 plans
Plans:
- [ ] 22-01-PLAN.md — Fix ADX NaN identity comparison (PROF-04) and Bollinger ddof (ROBU-02) with indicator tests
- [ ] 22-02-PLAN.md — Fix bar_volumes from candle data (PROF-03) and index_price sourcing with graceful degradation (ROBU-05)

### Phase 23: Sizing & Execution Optimization
**Goal**: Trades are sized large enough to overcome fee drag and protective orders minimize execution costs — the system is net-profitable on a per-trade basis after fees
**Depends on**: Phase 22
**Requirements**: PROF-01, PROF-02, PROF-05, ROBU-06
**Success Criteria** (what must be TRUE):
  1. A strategy signal with conviction 0.5 produces a position size whose expected profit (based on historical win rate and average win) exceeds estimated round-trip fees
  2. Stop-loss orders are placed as STOP_LIMIT with a configurable limit buffer (e.g., 0.1%) below the stop price, paying maker fee instead of taker fee
  3. The risk engine rejects any proposed trade where estimated round-trip fees (entry + exit) exceed the signal's expected edge, logging the rejection reason
  4. BTC-PERP either has a higher max_position_notional_usdc cap or OBI strategy has a longer cooldown — whichever is configured, the result is fewer fee-negative high-frequency BTC trades
**Plans**: 2 plans
Plans:
- [x] 21-01-PLAN.md — Fix double execution (SAFE-01), metadata serialization (SAFE-03), leverage constant (SAFE-04)
- [x] 21-02-PLAN.md — Kill switch regression test (SAFE-02), Route B credentials (SAFE-05)

### Phase 24: Risk Engine Enhancements
**Goal**: The risk engine prevents concentrated directional bets across correlated instruments and tracks true peak-to-trough drawdown for kill switch decisions
**Depends on**: Phase 21
**Requirements**: ROBU-01, ROBU-04
**Success Criteria** (what must be TRUE):
  1. The risk engine rejects a new LONG ETH-PERP trade when existing LONG positions in BTC-PERP and SOL-PERP already exceed a configurable net directional exposure threshold
  2. The equity high-water mark is updated on every portfolio snapshot and the drawdown kill switch compares current equity to the true all-time peak — not a daily-reset proxy
  3. The correlation exposure check and HWM drawdown are both configurable via YAML and can be disabled per-route without code changes
**Plans**: 2 plans
Plans:
- [ ] 21-01-PLAN.md — Fix double execution (SAFE-01), metadata serialization (SAFE-03), leverage constant (SAFE-04)
- [ ] 21-02-PLAN.md — Kill switch regression test (SAFE-02), Route B credentials (SAFE-05)

### Phase 25: Paper Simulator Fidelity
**Goal**: Paper mode results approximate real-world execution quality — fills are not guaranteed, adverse selection is modeled, and stop-loss orders experience realistic slippage
**Depends on**: Phase 23
**Requirements**: ROBU-03
**Success Criteria** (what must be TRUE):
  1. The paper simulator does not fill 100% of limit orders instantly — fill probability depends on price proximity to the order price and available volume
  2. Filled orders experience adverse selection: the average fill price is worse than the order price by a configurable amount reflecting typical market impact
  3. Stop-loss orders in paper mode experience configurable slippage (e.g., 0.05-0.15%) reflecting real-world stop execution in fast markets
**Plans**: 2 plans
Plans:
- [ ] 21-01-PLAN.md — Fix double execution (SAFE-01), metadata serialization (SAFE-03), leverage constant (SAFE-04)
- [ ] 21-02-PLAN.md — Kill switch regression test (SAFE-02), Route B credentials (SAFE-05)

## Progress

**Execution Order:**
Phases execute in numeric order: 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18 -> 19 -> 20 -> 21 -> 22 -> 23 -> 24 -> 25

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
| 21. Safety Critical Fixes | v1.4 | 2/2 | Complete    | 2026-04-08 |
| 22. Data Pipeline Fixes | v1.4 | 0/? | Not started | - |
| 23. Sizing & Execution Optimization | v1.4 | 0/? | Not started | - |
| 24. Risk Engine Enhancements | v1.4 | 0/? | Not started | - |
| 25. Paper Simulator Fidelity | v1.4 | 0/? | Not started | - |
