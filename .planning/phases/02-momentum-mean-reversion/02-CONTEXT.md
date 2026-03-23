# Phase 2: Momentum and Mean Reversion Improvements - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

The two highest-frequency existing strategies produce higher-quality signals with fewer false positives and can route high-conviction signals to Portfolio A for autonomous execution. This phase improves momentum (volume confirmation, adaptive conviction, structure-aware stops) and mean reversion (multi-factor trend rejection, adaptive bands, extended targets). Both strategies get Portfolio A routing for their best signals.

</domain>

<decisions>
## Implementation Decisions

### Portfolio A conviction thresholds
- **D-01:** Conviction thresholds for Portfolio A routing are at Claude's discretion — pick values that make sense for the signal quality
- **D-02:** Momentum requires a higher conviction threshold for Portfolio A than mean reversion — breakouts fail more often and need more confirmation
- **D-03:** Routing decision is conviction-only — no per-instrument routing differences, keep it simple
- **D-04:** Position sizing and notional caps for Portfolio A signals are entirely the risk agent's responsibility — strategies just set `suggested_target=PortfolioTarget.A` above the threshold

### Momentum re-enablement
- **D-05:** Momentum gets a higher weight than mean reversion (currently 0.15) when re-enabled — it's typically more frequent
- **D-06:** Enable momentum for all 5 instruments immediately — no gradual rollout
- **D-07:** Fix the YAML loader bugs as part of Phase 2 — missing fields (adx_threshold, adx_period, cooldown_bars, stop_loss_atr_mult, take_profit_atr_mult) must be loaded from config
- **D-08:** Defer per-instrument parameter tuning for momentum until the strategy logic stabilizes — use reasonable defaults for now, but track tuning as a future task

### Mean reversion extended targets
- **D-09:** Set `take_profit` to the extended target price; note the partial exit level (band middle/mean) in signal metadata for the execution layer
- **D-10:** Definition of "strong" reversion and the extended target placement are at Claude's discretion — pick what's most logical and likely to be profitable
- **D-11:** Uniform partial/extended target logic across all instruments — let the per-instrument band widths (already tuned in Phase 1) handle differences naturally

### Volume confirmation sensitivity
- **D-12:** Reject momentum crossovers when bar_volume is below a rolling average — not on any decline, but on significant underperformance vs recent average
- **D-13:** Rolling average window size is at Claude's discretion, but should be small (not a huge lookback)
- **D-14:** Volume should also boost conviction when surging — not just filter on decline, but reward increasing volume with higher conviction scores
- **D-15:** Mean reversion also gets volume confirmation — high volume on a band touch confirms reversion strength and should boost conviction

### Claude's Discretion
- Specific Portfolio A conviction thresholds for momentum and mean reversion (D-01, D-02)
- Momentum weight value (D-05 — higher than 0.15, exact number TBD)
- Definition of "strong" reversion for extended targets (D-10)
- Extended target placement formula (D-10)
- Volume rolling average lookback window (D-13)
- Volume boost formula for conviction (D-14, D-15)
- Swing point detection algorithm for structure-aware stops (MOM-03)
- Multi-factor trend rejection formula for mean reversion (MR-01)
- Adaptive band width scaling approach (MR-02)

</decisions>

<specifics>
## Specific Ideas

- Momentum YAML config has loader bugs — several parameters defined in code defaults aren't loaded from the config file (adx_threshold, adx_period, cooldown_bars, stop_loss_atr_mult, take_profit_atr_mult)
- Both strategies currently hardcode `suggested_target=PortfolioTarget.B` — Phase 2 adds conditional A routing
- FeatureStore `bar_volumes` property (added in Phase 1) is available but unused by either strategy
- Mean reversion currently takes profit at band middle only — losing upside on strong reversions
- Momentum conviction model (ADX strength + RSI agreement) is reasonable but doesn't use volatility context
- Phase 1 lowered all min_conviction values to 0.30-0.40 range for more activity (D-04 from Phase 1)
- Per-instrument momentum tuning deferred — track as future task so it's not forgotten

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy implementations (being modified)
- `agents/signals/strategies/momentum.py` — Full momentum strategy, conviction model, stop placement, cooldown logic
- `agents/signals/strategies/mean_reversion.py` — Full mean reversion strategy, ADX filter, band logic, take-profit at mean
- `agents/signals/strategies/base.py` — SignalStrategy abstract base class interface

### Strategy configs
- `configs/strategies/momentum.yaml` — Currently disabled, missing several parameter fields, no per-instrument overrides
- `configs/strategies/mean_reversion.yaml` — Enabled with full per-instrument overrides from Phase 1
- `configs/strategy_matrix.yaml` — Strategy-instrument enablement matrix (Phase 1)

### Signal model and routing
- `libs/common/models/signal.py` — StandardSignal fields: suggested_target, conviction, stop_loss, take_profit, metadata
- `libs/common/models/enums.py` — PortfolioTarget.A, PortfolioTarget.B, SignalSource enum

### Data available for strategies
- `agents/signals/feature_store.py` — FeatureStore: closes, highs, lows, volumes, bar_volumes, timestamps, open_interests, orderbook_imbalances, funding_rates

### Strategy loading and instantiation
- `agents/signals/main.py` — Strategy registration, per-instrument instantiation, config loading, validation integration
- `libs/common/config.py` — Config loading, validate_strategy_config(), log_config_diff()

### Constants
- `libs/common/constants.py` — Instrument specs, safety constants

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `FeatureStore.bar_volumes` (Phase 1): np.diff of 24h rolling volume — ready for volume confirmation logic
- `FeatureStore.highs` / `FeatureStore.lows`: Available for swing point detection (MOM-03 structure-aware stops)
- Mean reversion per-instrument config pattern: Full working example of instrument-specific tuning to replicate for momentum later
- Config validation (`validate_strategy_config()`): Will catch any new parameter keys added to momentum

### Established Patterns
- Conviction model: component1 (0-0.5) + component2 (0-0.5) = max 1.0 — both strategies use this
- Signal emission: `StandardSignal(signal_id=..., suggested_target=..., metadata={...})` — metadata dict carries indicator values
- Portfolio routing: `suggested_target=PortfolioTarget.A` or `B` — alpha combiner respects this hint
- ATR-based stops: `entry ± (multiplier × ATR)` — currently used by both, being replaced by swing points for momentum

### Integration Points
- `suggested_target` field on StandardSignal — consumed by alpha combiner for portfolio routing
- `metadata` dict on StandardSignal — can carry partial_target, volume_confirmation, volatility_percentile for execution layer
- Strategy matrix in `configs/strategy_matrix.yaml` — momentum must be enabled here when re-enabled
- Momentum YAML loader in `__init__` — needs additional `self._params.get()` calls for missing fields

</code_context>

<deferred>
## Deferred Ideas

- Per-instrument momentum parameter tuning — defer until strategy logic stabilizes, track as future task (D-08)
- Adaptive conviction thresholds scaling with volatility percentile — Phase 5 (XQ-01) provides shared utility; Phase 2 implements MOM-02 inline
- Session-aware parameter profiles — Phase 5 (XQ-02, XQ-03)
- Swing point detection as shared utility — Phase 5 (XQ-05) may extract to reusable module; Phase 2 implements inline for momentum

</deferred>

---

*Phase: 02-momentum-mean-reversion*
*Context gathered: 2026-03-21*
