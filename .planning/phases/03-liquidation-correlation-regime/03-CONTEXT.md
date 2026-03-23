# Phase 3: Liquidation, Correlation, and Regime Improvements - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

The remaining three existing strategies (liquidation cascade, correlation, regime trend) produce more nuanced signals with graduated responses, multi-window analysis, and adaptive thresholds. Liquidation gets tiered response levels with volume confirmation. Correlation gets multi-window basis analysis with funding rate integration. Regime trend gets adaptive filter thresholds and trailing stop metadata. High-conviction correlation signals route to Portfolio A.

</domain>

<decisions>
## Implementation Decisions

### Liquidation cascade tiers
- **D-01:** Aggressive position sizing on severe cascades — tier 3 (>8% OI drop) should take larger positions with wider stops, not conservative across all tiers
- **D-02:** Tier boundaries as specified in requirements: Tier 1 (2-4% OI drop), Tier 2 (4-8%), Tier 3 (>8%)
- **D-03:** Volume surge confirmation required alongside OI drops to distinguish forced liquidation from organic reduction (LIQ-02)
- **D-04:** Liquidation cascade remains disabled for QQQ/SPY (Phase 1, D-11 — crypto-native strategy)

### Correlation multi-window agreement
- **D-05:** Fire when 2 of 3 windows agree, provided funding rate favors the same direction — funding acts as a confirming tiebreaker, not a standalone trigger
- **D-06:** If all 3 windows agree, fire regardless of funding rate direction — unanimous basis agreement is strong enough on its own
- **D-07:** Funding rate integration creates a three-factor model: short/medium/long basis windows + funding rate direction alignment

### Regime trend trailing stops
- **D-08:** Full discretion to Claude on trailing stop metadata design — trail parameters, initial stop tightness, and metadata format
- **D-09:** Adaptive ADX and ATR expansion thresholds adjust with volatility regime — implementation details at Claude's discretion

### Portfolio A routing
- **D-10:** High-conviction correlation signals route to Portfolio A — follows the pattern established in Phase 2 (conviction threshold → suggested_target=PortfolioTarget.A)
- **D-11:** Portfolio A conviction threshold for correlation at Claude's discretion — should reflect the multi-window + funding agreement quality

### Claude's Discretion
- Exact position sizing multipliers per liquidation tier (D-01 — aggressive on severe)
- Stop width multipliers per liquidation tier
- Volume surge threshold for liquidation confirmation (D-03)
- Correlation Portfolio A conviction threshold (D-11)
- How funding rate weight interacts with basis window agreement (D-05, D-07)
- Trailing stop trail parameters and metadata format (D-08)
- Adaptive threshold scaling formulas for regime trend (D-09)
- Whether regime trend also gets Portfolio A routing (not in requirements — leave as-is if not)

</decisions>

<specifics>
## Specific Ideas

- Phase 2 established reusable patterns: volume confirmation via bar_volumes rolling average, adaptive conviction via scipy.stats.percentileofscore, Portfolio A routing via conviction threshold
- These same patterns should be applied where applicable in Phase 3 strategies
- Liquidation cascade is inherently short-term — aggressive sizing on severe cascades aligns with catching forced selling events

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy implementations (being modified)
- `agents/signals/strategies/liquidation_cascade.py` — Current liquidation cascade strategy, OI drop detection, orderbook imbalance
- `agents/signals/strategies/correlation.py` — Current correlation strategy, basis divergence, OI/price divergence
- `agents/signals/strategies/regime_trend.py` — Current regime trend strategy, triple filter, breakout/pullback

### Strategy configs (already have per-instrument overrides from Phase 1)
- `configs/strategies/liquidation_cascade.yaml` — Enabled for crypto, disabled for QQQ/SPY, per-instrument overrides
- `configs/strategies/correlation.yaml` — Enabled for all instruments, per-instrument overrides
- `configs/strategies/regime_trend.yaml` — Enabled for all instruments, extensive per-instrument overrides

### Phase 2 reference implementations (patterns to reuse)
- `agents/signals/strategies/momentum.py` — Volume confirmation pattern (bar_volumes rolling avg), adaptive conviction (percentileofscore), swing stop placement, Portfolio A routing
- `agents/signals/strategies/mean_reversion.py` — Multi-factor trend rejection pattern, adaptive parameter scaling, extended targets with metadata, Portfolio A routing

### Signal model and routing
- `libs/common/models/signal.py` — StandardSignal: suggested_target, conviction, stop_loss, take_profit, metadata
- `libs/common/models/enums.py` — PortfolioTarget.A, PortfolioTarget.B

### Data available
- `agents/signals/feature_store.py` — FeatureStore: closes, highs, lows, volumes, bar_volumes, timestamps, open_interests, orderbook_imbalances, funding_rates

### Config and matrix
- `configs/strategy_matrix.yaml` — Strategy-instrument enablement matrix
- `libs/common/config.py` — Config loading, validation, diff logging

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets from Phase 2
- Volume confirmation pattern: `store.bar_volumes` → rolling average → reject/boost based on ratio — directly applicable to LIQ-02 volume surge confirmation
- Adaptive conviction with `scipy.stats.percentileofscore` on ATR history — applicable to RT-01 adaptive thresholds
- Portfolio A routing: `suggested_target = PortfolioTarget.A if conviction >= threshold else PortfolioTarget.B` — applicable to CORR-03
- Signal metadata dict for execution layer hints — applicable to RT-02 trailing stop parameters

### Established Patterns
- 3-component conviction model (Phase 2 momentum): can be adapted for correlation's three-factor model (short basis + medium basis + long basis/funding)
- Per-instrument YAML overrides with config validation — all three strategies already have these from Phase 1
- TDD approach: write tests first, implement, verify — established in both Phase 1 and Phase 2

### Integration Points
- `FeatureStore.open_interests` — consumed by liquidation cascade for OI drop detection
- `FeatureStore.funding_rates` — consumed by correlation for funding rate integration (CORR-02)
- `FeatureStore.bar_volumes` — available for volume surge confirmation (LIQ-02)
- Strategy YAML configs already have per-instrument overrides — new parameters added to existing `parameters:` blocks

</code_context>

<deferred>
## Deferred Ideas

- Regime trend Portfolio A routing — not in Phase 3 requirements, could be added in Phase 5 cross-cutting quality
- Liquidation cascade for equity perps — deferred indefinitely (crypto-native pattern)
- Shared swing point detection utility — Phase 5 (XQ-05) may extract from momentum's inline implementation

</deferred>

---

*Phase: 03-liquidation-correlation-regime*
*Context gathered: 2026-03-22*
