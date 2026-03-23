# Phase 1: Foundation and Per-Instrument Tuning - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Infrastructure prerequisites, bug fixes, and per-instrument YAML configs for all 5 instruments (ETH, BTC, SOL, QQQ, SPY). The system has all plumbing ready and every instrument trades with asset-appropriate thresholds instead of universal defaults. No strategy logic changes — those are Phases 2-5.

</domain>

<decisions>
## Implementation Decisions

### Per-instrument tuning approach
- **D-01:** Derive parameter values from known asset characteristics — ETH volatility profile, BTC liquidity depth, SOL high-vol/thin-book, QQQ/SPY equity dynamics
- **D-02:** Treat each instrument independently — no "reference" instrument with relative scaling; each gets its own research-informed parameter set
- **D-03:** Completely separate parameter sets for crypto vs equity perps — not mostly-shared with overrides
- **D-04:** Actively lower thresholds to increase signal frequency so performance can be evaluated — the current paper trading showed too little activity, especially on weekends

### Config validation strictness
- **D-05:** Error and halt on unknown YAML keys at the base level — typos must not silently use defaults
- **D-06:** Validate the entire YAML structure (top-level keys like `enabled`, `instruments`, `parameters`), not just the parameters block
- **D-07:** Per-instrument override keys also validated, but warn instead of halt — less strict than base level
- **D-08:** Only flag unknown keys — no special handling for deprecated/removed parameters

### Strategy enablement per instrument
- **D-09:** Momentum stays globally disabled (`enabled: false`) until Phase 2 improves it
- **D-10:** Create a formalized "strategy matrix" — explicit declaration of which strategies run on which instruments, rather than each strategy YAML independently deciding
- **D-11:** QQQ/SPY enablement for liquidation cascade and correlation strategies left to Claude's discretion during planning based on whether OI/basis dynamics apply to equity perps

### Equity perp session handling
- **D-12:** No session logic in Phase 1 — avoid any market-hours gating or session classification until Phase 5 delivers it properly
- **D-13:** QQQ/SPY perps are active on weekends too — configs should not assume market-hours-only operation
- **D-14:** Single parameter profile per instrument — no weekday/weekend split until Phase 5's session classifier
- **D-15:** Let natural low volume/volatility suppress signals organically outside active hours rather than adding explicit gates

### Claude's Discretion
- Whether liquidation cascade should be enabled for QQQ-PERP and SPY-PERP (D-11)
- Whether correlation strategy should be disabled for equity perps
- Strategy matrix format and location (new file vs embedded in existing config)
- Exact parameter values per instrument (research-informed, but specific numbers are implementation detail)
- How config schema validation is implemented (Pydantic model vs manual checks)

</decisions>

<specifics>
## Specific Ideas

- Paper trading on ETH-PERP showed very low activity, especially on weekends — thresholds are too conservative for current market conditions
- The goal is more signals to evaluate, not fewer false positives — tuning for activity over precision at this stage

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Config system
- `libs/common/config.py` — Config loading, YAML parsing, `load_strategy_config_for_instrument()` merge logic
- `configs/default.yaml` — Default config structure, instrument specs, fee tiers

### Strategy configs (all need per-instrument tuning)
- `configs/strategies/momentum.yaml` — Currently disabled; leave as-is per D-09
- `configs/strategies/mean_reversion.yaml` — Enabled, minimal params, no per-instrument overrides yet
- `configs/strategies/liquidation_cascade.yaml` — Enabled for crypto, disabled for QQQ/SPY
- `configs/strategies/correlation.yaml` — Enabled everywhere, no per-instrument overrides
- `configs/strategies/regime_trend.yaml` — Enabled with extensive per-instrument overrides (reference pattern)

### Signal infrastructure
- `libs/common/models/enums.py` — SignalSource enum, needs VWAP + VOLUME_PROFILE entries
- `agents/signals/feature_store.py` — FeatureStore class, needs timestamps accessor + bar_volume deltas
- `agents/signals/main.py` — Strategy loading, per-instrument instantiation, cooldown tracking location

### Strategy base and implementations
- `agents/signals/strategies/base.py` — SignalStrategy abstract base class
- `agents/signals/strategies/momentum.py` — Cooldown tracking pattern (`_bars_since_signal`)

### Dependencies
- `pyproject.toml` — Current dependency list, needs scipy + bottleneck additions

### Constants
- `libs/common/constants.py` — Instrument specs, safety constants

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `regime_trend.yaml` per-instrument override pattern: full working example of `instruments: <iid>: parameters:` structure to replicate across all strategy configs
- `load_strategy_config_for_instrument()` in config.py: already handles shallow merge of per-instrument overrides — validation layer adds on top
- Per-instrument strategy instantiation in `agents/signals/main.py` lines 89-141: each instrument already gets its own strategy instances

### Established Patterns
- Strategy configs follow consistent YAML structure: `name`, `enabled`, `parameters`, `instruments`
- FeatureStore properties return `NDArray` from internal deques — new properties (timestamps, bar_volume) follow same pattern
- Cooldown uses `_bars_since_signal` counter per strategy instance — needs refactoring to dict keyed by instrument_id

### Integration Points
- Config validation hooks into `load_strategy_config()` and `load_strategy_config_for_instrument()` in `libs/common/config.py`
- FeatureStore changes are consumed by all strategies via `evaluate(snapshot, store)` — additive properties don't break existing code
- SignalSource enum additions are consumed by alpha combiner for routing — new entries are inert until Phase 4 strategies use them
- Strategy matrix would be a new concept consumed by `agents/signals/main.py` during strategy instantiation

</code_context>

<deferred>
## Deferred Ideas

- Session-aware parameter profiles (weekday/weekend, market hours/off hours) — Phase 5 (XQ-02, XQ-03)
- Adaptive conviction thresholds that scale with volatility — Phase 5 (XQ-01)
- Volume profile strategy using bar_volume data — Phase 4 (deferred to v2)

</deferred>

---

*Phase: 01-foundation-tuning*
*Context gathered: 2026-03-21*
