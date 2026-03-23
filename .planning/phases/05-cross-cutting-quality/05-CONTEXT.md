# Phase 5: Cross-Cutting Quality - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

All strategies benefit from shared utilities for adaptive conviction, session awareness, conviction normalization, and structure-aware stops. Extract common patterns implemented inline in Phases 2-4 into reusable utilities. Also includes per-instrument tuning for strategies whose logic changed significantly (deferred momentum tuning from Phase 2 D-08, plus any other strategies needing refresh).

</domain>

<decisions>
## Implementation Decisions

### Session classifier and session-aware params
- **D-01:** Significantly different parameters for weekend vs weekday crypto — not modest adjustments. Weekend/off-hours sessions need tweaked parameters to still take trades when opportunities present despite low activity
- **D-02:** Session-aware parameters in a separate config file — not a new layer in existing per-instrument YAML. Keeps session config cleanly separated from base strategy configs
- **D-03:** Tune parameters that affect trade frequency in quiet sessions — min_conviction, cooldown_bars, band widths, stop multipliers, and any other params that gate signal emission
- **D-04:** All 7 strategies get session-aware params — momentum, mean reversion, liquidation cascade, correlation, regime trend, orderbook imbalance, VWAP

### Conviction normalization
- **D-05:** Post-processing step that maps raw conviction to normalized bands — do not rewrite internal conviction models. Safer at this stage, avoids breaking tested logic
- **D-06:** Conviction bands: low (0.3-0.5), medium (0.5-0.7), high (0.7-1.0)
- **D-07:** Conviction normalization affects Portfolio A routing — unify to a single "high band" threshold instead of per-strategy thresholds (currently momentum 0.75, mean reversion 0.65, correlation 0.70)
- **D-08:** Acceptable that raw conviction means different things across strategies — normalization provides a consistent overlay, not a rewrite of how each strategy computes conviction

### Deferred tuning
- **D-09:** Include per-instrument momentum tuning in Phase 5 — the logic is now stable (volume confirmation, adaptive conviction, swing stops, funding boost)
- **D-10:** Follow the same research-informed approach as Phase 1 tuning — derive values from known asset characteristics, completely separate per instrument, lower thresholds for activity
- **D-11:** Refresh per-instrument params for other strategies whose logic changed significantly in Phases 2-4 if meaningful — correlation (multi-window + funding), regime trend (adaptive thresholds), and any others where the Phase 1 tuning no longer matches the updated logic

### Claude's Discretion
- Session classifier implementation (4 session types: crypto_weekday, crypto_weekend, equity_market_hours, equity_off_hours)
- Which specific parameters change per session and by how much (D-03)
- Session config file format and location
- How the post-processing conviction normalizer integrates into the signal pipeline
- Unified Portfolio A routing threshold value (D-07)
- Swing point detection utility API design (extracting from momentum's inline implementation)
- Adaptive conviction utility API design (extracting from inline percentileofscore usage)
- Which strategies need tuning refreshes beyond momentum (D-11)
- Specific per-instrument parameter values for momentum and refreshed strategies

</decisions>

<specifics>
## Specific Ideas

- Momentum's `_find_swing_low`/`_find_swing_high` (Phase 2) is the template for the shared swing point utility (XQ-05)
- `scipy.stats.percentileofscore` used inline in 4 strategies — extract to shared adaptive conviction utility (XQ-01)
- Phase 1's per-instrument tuning philosophy: research-informed, independent per instrument, lower thresholds for activity — applies to the deferred momentum tuning
- Alpha combiner is flagged as "untouched" in STATE.md — conviction normalization post-processing should NOT require alpha combiner changes (it's a strategy-level utility)
- VWAP strategy has session-aware reset already (session_reset_hour_utc) — session classifier should integrate with this existing mechanism

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategies to integrate with (all 7)
- `agents/signals/strategies/momentum.py` — Inline swing points, inline adaptive conviction, funding boost, Portfolio A at 0.75
- `agents/signals/strategies/mean_reversion.py` — Inline adaptive bands, funding boost, Portfolio A at 0.65
- `agents/signals/strategies/liquidation_cascade.py` — Tiered cascade, volume surge
- `agents/signals/strategies/correlation.py` — Multi-window basis, funding integration, Portfolio A at 0.70
- `agents/signals/strategies/regime_trend.py` — Adaptive thresholds via percentileofscore
- `agents/signals/strategies/orderbook_imbalance.py` — OBI strategy, Portfolio A routing
- `agents/signals/strategies/vwap.py` — VWAP with session-aware reset

### Existing shared utilities
- `agents/signals/funding_filter.py` — Shared funding rate utility (Phase 4 pattern for extracting to utility)
- `agents/signals/feature_store.py` — FeatureStore data available

### Configuration
- `configs/strategies/*.yaml` — Per-strategy configs with per-instrument overrides
- `configs/strategy_matrix.yaml` — Strategy-instrument enablement
- `libs/common/config.py` — Config loading, validation

### Signal model
- `libs/common/models/signal.py` — StandardSignal with suggested_target, conviction
- `libs/common/models/enums.py` — PortfolioTarget, SignalSource

### Phase 1 tuning reference
- `configs/strategies/regime_trend.yaml` — Most extensive per-instrument overrides (reference for momentum tuning)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets (to extract into shared utilities)
- Momentum's `_find_swing_low` / `_find_swing_high` → shared swing point utility (XQ-05)
- `scipy.stats.percentileofscore` used in momentum, mean reversion, regime trend, VWAP → shared adaptive conviction utility (XQ-01)
- `funding_filter.py` pattern (Phase 4) — demonstrates how to create a shared utility callable by multiple strategies

### Established Patterns
- Shared utility pattern: `agents/signals/funding_filter.py` — function-based utility returning a dataclass result
- Per-instrument config merging: `load_strategy_config_for_instrument()` in config.py
- Portfolio A routing: `suggested_target = PortfolioTarget.A if conviction >= threshold`
- Strategy registration: STRATEGY_CLASSES dict + strategy_matrix.yaml

### Integration Points
- Conviction normalization post-processor would sit between strategy `evaluate()` output and signal emission — or as a utility each strategy calls before returning signals
- Session classifier would be a utility in `libs/` or `agents/signals/` that strategies call to get current session type
- Session config loaded at strategy startup alongside existing per-instrument config
- Swing point utility replaces momentum's inline implementation and is imported by mean reversion, regime trend

</code_context>

<deferred>
## Deferred Ideas

- Volume profile strategy (VPRO-01 through VPRO-03) — v2 requirement
- Alpha combiner improvements (ALPHA-01 through ALPHA-03) — v2 requirement
- Multi-timeframe FeatureStore (ADV-01) — v2 requirement
- Trailing stop state management in execution layer (ADV-02) — v2 requirement

</deferred>

---

*Phase: 05-cross-cutting-quality*
*Context gathered: 2026-03-22*
