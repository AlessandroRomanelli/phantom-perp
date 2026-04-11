# Roadmap: Phantom Perp

## Milestones

- ✅ **v1.0 Strategy Enhancement** - Phases 1-5 (shipped 2026-03-22)
- ✅ **v1.1 Multi-Instrument Ingestion** - Phases 6-9.1 (shipped 2026-03-23)
- ✅ **v1.3 Concerns Resolution** - Phases 16-20 (shipped 2026-04-08)
- ✅ **v1.4 Forensic Audit Fixes** - Phases 21-25 (shipped 2026-04-09)
- 🔄 **v1.5 Local Claude CLI Integration** - Phases 26-28 (in progress)

## Phases

<details>
<summary>✅ v1.0 Strategy Enhancement (Phases 1-5) — SHIPPED 2026-03-22</summary>

See MILESTONES.md for v1.0 accomplishments.

</details>

<details>
<summary>✅ v1.1 Multi-Instrument Ingestion (Phases 6-9.1) — SHIPPED 2026-03-23</summary>

See MILESTONES.md for v1.1 accomplishments.

</details>

<details>
<summary>✅ v1.3 Concerns Resolution (Phases 16-20) — SHIPPED 2026-04-08</summary>

See MILESTONES.md for v1.3 accomplishments.

</details>

<details>
<summary>✅ v1.4 Forensic Audit Fixes (Phases 21-25) — SHIPPED 2026-04-09</summary>

See MILESTONES.md for v1.4 accomplishments.

</details>

- [x] **Phase 26: JSON Extraction Foundation** - Shared utility and prompt engineering for structured CLI output (completed 2026-04-09)
- [x] **Phase 27: CLI Call Site Migration** - Replace all 3 Anthropic SDK call sites with claude -p subprocess calls (completed 2026-04-09)
- [ ] **Phase 28: Dependency Cleanup** - Remove anthropic SDK, ANTHROPIC_API_KEY, and migrate tests

## Phase Details

### Phase 16: Centralized Deserialization
**Goal**: All agent deserialization logic is consolidated into a single shared module in libs, eliminating copy-paste drift across 5 agents
**Depends on**: Phase 9.1 (v1.1 shipped)
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
- [x] 22-01-PLAN.md — Fix ADX NaN identity comparison (PROF-04) and Bollinger ddof (ROBU-02) with indicator tests
- [x] 22-02-PLAN.md — Fix bar_volumes from candle data (PROF-03) and index_price sourcing with graceful degradation (ROBU-05)

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
- [x] 23-01-PLAN.md — Config changes (conviction_power, OBI cooldown) and STOP_LIMIT stop-loss with maker fees
- [x] 23-02-PLAN.md — Fee-adjusted signal filter in risk engine

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
- [x] 24-01-PLAN.md — Correlation exposure check (ROBU-01)
- [x] 24-02-PLAN.md — HWM drawdown tracking (ROBU-04)

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
- [x] 25-01-PLAN.md — Probabilistic fill model with adverse selection (TDD)
- [x] 25-02-PLAN.md — Stop-loss slippage in protective order monitor (TDD)

### Phase 26: JSON Extraction Foundation
**Goal**: A shared, tested utility extracts and validates JSON from Claude CLI text output, and all three call site prompts are engineered to produce JSON code blocks
**Depends on**: Phase 25 (v1.4 shipped)
**Requirements**: PROMPT-02, PROMPT-01
**Success Criteria** (what must be TRUE):
  1. A shared `extract_json` utility (e.g., `libs/tuner/json_extractor.py` or equivalent) parses JSON from markdown-fenced code blocks in CLI text output, handling extraneous prose before and after the block
  2. The utility raises a descriptive exception (not a silent `None`) when no valid JSON block is found, enabling callers to distinguish parse failure from a valid empty result
  3. Each of the three call site prompts explicitly instructs Claude to respond with a JSON code block matching the expected schema — the prompt text is committed and verifiable in the source files
  4. Unit tests for the extraction utility cover: clean JSON block, fenced with ```json, extraneous prose around the block, multiple blocks (first wins), and no block present
**Plans**: 2 plans
Plans:
- [x] 26-01-PLAN.md — JSON extraction utility with TDD test suite (libs/common/json_extractor.py)
- [x] 26-02-PLAN.md — Update all 3 call site prompts with JSON code block output instructions

### Phase 27: CLI Call Site Migration
**Goal**: All three Anthropic SDK call sites are replaced with claude -p subprocess calls that return identical data structures to their callers
**Depends on**: Phase 26
**Requirements**: CLI-01, CLI-02, CLI-03
**Success Criteria** (what must be TRUE):
  1. `libs/tuner/claude_client.py` invokes `claude -p` via `subprocess.run()` and returns a `dict` with `summary` and `recommendations` keys — the calling code in the tuner is unchanged
  2. `agents/signals/claude_market_client.py` invokes `claude -p` via `asyncio.create_subprocess_exec()` and returns the same validated analysis dict the strategy evaluation code already expects
  3. `agents/signals/orch_client.py` invokes `claude -p` via `asyncio.create_subprocess_exec()` and returns the same decisions list shape — no changes required in the orchestrator consumer
  4. A subprocess timeout is configured on all three call sites so a hanging CLI process does not stall the trading pipeline indefinitely
  5. No `import anthropic` statements remain anywhere in the codebase
**Plans:** 2/2 plans complete
Plans:
- [x] 27-01-PLAN.md — Migrate tuner call_claude() from Anthropic SDK to subprocess.run()
- [x] 27-02-PLAN.md — Migrate async signal call sites (claude_market_client, orch_client) to asyncio.create_subprocess_exec()
**UI hint**: no

### Phase 28: Dependency Cleanup & Test Migration
**Goal**: The anthropic package and its API key requirement are fully removed from the project, and all affected tests mock subprocess instead of the SDK
**Depends on**: Phase 27
**Requirements**: DEP-01, DEP-02, DEP-03
**Success Criteria** (what must be TRUE):
  1. `pyproject.toml` has no reference to `anthropic` in any dependency group — `pip install` of the project does not install the Anthropic SDK
  2. `ANTHROPIC_API_KEY` does not appear in any Dockerfile, `.env` template, `docker-compose.yml`, or documentation file
  3. All tests for the three migrated call sites mock `subprocess.run` (tuner) and `asyncio.create_subprocess_exec` (signals) — no test imports or patches `anthropic`
  4. The full test suite passes (`pytest`) with the anthropic package absent from the environment
**Plans:** 2 plans
Plans:
- [x] 28-01-PLAN.md — Remove anthropic dep, migrate tuner tests, update manual_tune.py
- [x] 28-02-PLAN.md — Migrate signal test files (test_claude_market_analysis, test_orch_client)

## Progress

**Execution Order:**
Phases execute in numeric order: 16 -> 17 -> 18 -> 19 -> 20 -> 21 -> 22 -> 23 -> 24 -> 25 -> 26 -> 27 -> 28

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 16. Centralized Deserialization | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 17. Bug Fixes | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 18. Messaging Infrastructure | v1.3 | 1/1 | Complete    | 2026-04-08 |
| 19. Core Infrastructure Tests | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 20. Risk & Indicator Tests | v1.3 | 2/2 | Complete    | 2026-04-08 |
| 21. Safety Critical Fixes | v1.4 | 2/2 | Complete    | 2026-04-08 |
| 22. Data Pipeline Fixes | v1.4 | 2/2 | Complete    | 2026-04-08 |
| 23. Sizing & Execution Optimization | v1.4 | 2/2 | Complete    | 2026-04-08 |
| 24. Risk Engine Enhancements | v1.4 | 2/2 | Complete    | 2026-04-09 |
| 25. Paper Simulator Fidelity | v1.4 | 2/2 | Complete    | 2026-04-09 |
| 26. JSON Extraction Foundation | v1.5 | 2/2 | Complete    | 2026-04-09 |
| 27. CLI Call Site Migration | v1.5 | 2/2 | Complete    | 2026-04-09 |
| 28. Dependency Cleanup & Test Migration | v1.5 | 2/2 | Complete    | 2026-04-11 |

### Phase 29: Regime-aware strategy parameters

**Goal:** Strategy parameters dynamically adapt to the current market regime — each of the 7 strategies has YAML-configurable overrides for all 6 regimes, applied at evaluate-time using the same pattern as session overrides
**Requirements**: REG-01, REG-02, REG-03
**Depends on:** Phase 28
**Plans:** 2 plans

Plans:
- [ ] 29-01-PLAN.md — MarketSnapshot regime field, configs/regimes.yaml, load/lookup functions with tests
- [ ] 29-02-PLAN.md — Wire regime detection and override application into signals main loop
