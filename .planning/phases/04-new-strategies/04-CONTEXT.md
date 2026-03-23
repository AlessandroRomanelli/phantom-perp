# Phase 4: New Strategies - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Three new signal sources fill coverage gaps: a funding rate confirmation utility (boosts existing strategy signals), an orderbook imbalance strategy (short-term directional signals from bid/ask depth), and a VWAP deviation strategy (feasibility-gated — may be deferred to v2). Each uses data already available in FeatureStore.

</domain>

<decisions>
## Implementation Decisions

### Funding rate filter architecture
- **D-01:** Funding rate filter is a shared utility, not a standalone SignalStrategy — it boosts conviction for other strategies' signals when funding aligns with direction
- **D-02:** Generalize the pattern from Phase 3's correlation funding integration — make it callable by any strategy
- **D-03:** Boost only, never suppress — extreme funding aligned with signal direction increases conviction, but opposing funding does NOT reject the signal
- **D-04:** Opt-in per strategy — strategies call the utility explicitly, not automatically applied

### VWAP feasibility
- **D-05:** Claude validates feasibility programmatically — write a test/validation that checks whether the volume-delta approximation produces usable VWAP values, and auto-decide
- **D-06:** If VWAP approximation fails feasibility, defer the entire VWAP strategy to v2 with documented rationale — 2 of 3 new strategies (funding filter + orderbook imbalance) is an acceptable Phase 4 outcome
- **D-07:** Alternative VWAP approaches (e.g., rolling price-volume weighted average without session resets) are at Claude's discretion if the standard approach fails

### Orderbook imbalance behavior
- **D-08:** Shortest possible time horizon — push to the minimum practical given 60-second FeatureStore sampling (e.g., 1 hour or less)
- **D-09:** Fire often with varying conviction — catch more imbalance events rather than being highly selective, let conviction differentiate signal quality
- **D-10:** More conservative min_conviction than other strategies — orderbook data is noisier, so the bar to emit a signal should be higher even though it fires frequently
- **D-11:** Enable for all instruments including equity perps — let the minimum depth gate (OBI-03) naturally suppress signals on thin orderbooks rather than disabling per instrument

### Claude's Discretion
- Funding rate z-score computation details and thresholds (FUND-02)
- Time-to-funding decay formula (FUND-03)
- How the funding utility integrates with existing strategies (which strategies opt in first)
- VWAP feasibility test design and pass/fail criteria (D-05)
- Alternative VWAP approach if standard fails (D-07)
- Exact OBI time horizon (D-08 — shortest practical)
- OBI time-weighted imbalance lookback window (OBI-02)
- OBI minimum depth threshold (OBI-03)
- OBI Portfolio A conviction threshold (OBI-04)
- OBI conviction model design

</decisions>

<specifics>
## Specific Ideas

- Phase 3 already integrated funding rate into correlation strategy — the funding utility generalizes this pattern
- Funding rate data is sparse in FeatureStore (only sampled when value changes) — utility must handle empty/sparse arrays gracefully
- bar_volumes can be negative (rolling-off volume) — VWAP feasibility validation must account for this
- Orderbook imbalance is already sampled every 60s in FeatureStore — OBI strategy consumes existing data, no new ingestion needed
- VWAP-01 feasibility gate means the planner should structure VWAP work so feasibility is validated first, before investing in session resets and deviation signals

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### New strategy base and registration
- `agents/signals/strategies/base.py` — SignalStrategy abstract base class (OBI must implement this)
- `agents/signals/main.py` — STRATEGY_CLASSES registration, strategy matrix loading, per-instrument instantiation
- `configs/strategy_matrix.yaml` — Strategy-instrument enablement matrix (new strategies must be added)

### Existing strategies for reference patterns
- `agents/signals/strategies/correlation.py` — Funding rate integration pattern (Phase 3) to generalize as utility
- `agents/signals/strategies/momentum.py` — Volume confirmation, adaptive conviction, Portfolio A routing patterns
- `agents/signals/strategies/mean_reversion.py` — Extended targets with metadata pattern

### Data available
- `agents/signals/feature_store.py` — FeatureStore: orderbook_imbalances, funding_rates, bar_volumes, volumes, closes, timestamps, highs, lows
- `agents/ingestion/normalizer.py` — How orderbook_imbalance and funding_rate are populated from MarketSnapshot

### Signal model and routing
- `libs/common/models/signal.py` — StandardSignal: suggested_target, conviction, metadata
- `libs/common/models/enums.py` — SignalSource (FUNDING_ARB, ORDERBOOK_IMBALANCE, VWAP already exist), PortfolioTarget

### Strategy configs
- `configs/strategies/` — Existing strategy YAML pattern to replicate for new strategies
- `libs/common/config.py` — Config loading, validation, diff logging

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Correlation's funding rate integration (Phase 3): `store.funding_rates[-1]` with empty array guard — direct template for the shared utility
- Phase 2 conviction model patterns: 3-component models with clamping — applicable to OBI
- Portfolio A routing: `suggested_target = PortfolioTarget.A if conviction >= threshold else PortfolioTarget.B` — applicable to OBI
- `scipy.stats.percentileofscore` — already used in momentum, mean reversion, regime trend for adaptive thresholds
- FeatureStore `bar_volumes` property (Phase 1): np.diff of 24h volume — input for VWAP approximation

### Established Patterns
- New strategies register in STRATEGY_CLASSES dict in main.py
- New strategies get their own `configs/strategies/<name>.yaml` with per-instrument overrides
- Strategy matrix in `configs/strategy_matrix.yaml` controls per-instrument enablement
- Config validation via `validate_strategy_config()` catches unknown YAML keys
- TDD approach: write tests first, implement, verify

### Integration Points
- `STRATEGY_CLASSES` dict in `agents/signals/main.py` — new strategies register here
- `configs/strategy_matrix.yaml` — new strategies added with per-instrument enablement
- Funding utility would live in `libs/` or `agents/signals/` — needs to be importable by multiple strategies
- SignalSource enum already has FUNDING_ARB, ORDERBOOK_IMBALANCE, VWAP entries

</code_context>

<deferred>
## Deferred Ideas

- VWAP strategy may be fully deferred to v2 if feasibility validation fails (D-06)
- Volume profile strategy (VPRO-01 through VPRO-03) — already in v2 requirements
- Funding rate as standalone signal emitter — not needed since it's a utility (D-01)
- Per-instrument momentum tuning — still pending from Phase 2 (D-08)

</deferred>

---

*Phase: 04-new-strategies*
*Context gathered: 2026-03-22*
