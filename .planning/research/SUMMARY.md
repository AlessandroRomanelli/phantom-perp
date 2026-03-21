# Project Research Summary

**Project:** Phantom Perp — Strategy Enhancement
**Domain:** Perpetual futures signal generation (Coinbase INTX)
**Researched:** 2026-03-21
**Confidence:** HIGH

## Executive Summary

Phantom Perp is a medium-frequency signal generation bot for perpetual futures on Coinbase INTX. The system is architecturally mature: a well-factored strategy pipeline reads from per-instrument FeatureStores (deque-based rolling buffers of 500 samples at 60s intervals), emits `StandardSignal` objects to Redis, and feeds a downstream alpha combiner and execution layer. The strategy enhancement milestone is fundamentally an expansion problem, not a redesign problem. New strategies plug directly into the existing `SignalStrategy` base class contract, infrastructure changes amount to roughly 10 lines of additive code, and the highest-ROI work (per-instrument parameter tuning) is pure YAML configuration requiring zero code changes. The existing pure-numpy indicator library is complete and correct — no TA library additions are needed.

The recommended approach is to work outward from the foundation rather than adding complexity wholesale. Start by tuning existing strategies per-instrument (the single highest-ROI change), then add the two pre-wired new strategies (funding arb and orderbook imbalance, which already have enum slots, config files, and routing rules), then layer on strategy improvements (adaptive conviction, multi-window correlation, graduated liquidation cascade), and finish with cross-cutting quality work (conviction normalization, full Portfolio A routing). Two stack additions are warranted: `scipy` for statistical operations (z-scores, distribution tests) and `bottleneck` for fast rolling window computations.

The primary risks are systemic rather than implementation-level. Signal correlation cascade — where momentum, regime trend, and correlation strategies all fire LONG simultaneously on the same price move — is the most dangerous failure mode, as the alpha combiner amplifies what appears to be consensus but is actually correlated bets. The second risk is overfitting per-instrument parameters without a backtesting framework. A third urgent concern is a known paper simulator bug that skips hourly funding settlements, which must be fixed before any funding-aware strategy can be validly paper-tested. These risks are all addressable with disciplined implementation.

## Key Findings

### Recommended Stack

The core stack (Python 3.13, asyncio, numpy, polars, redis, pydantic, structlog) is unchanged and sufficient. Two new dependencies are warranted for the strategy enhancement milestone.

**Core technologies:**
- `scipy>=1.17,<2`: Statistical computation — z-scores, percentile ranks, distribution tests for funding arb, adaptive conviction, and regime detection. The undisputed standard; no lighter alternative covers this breadth.
- `bottleneck>=1.6,<2`: Fast rolling window operations — `move_mean`, `move_std`, `move_rank` are 100-6000x faster than numpy loops for rolling z-scores and correlations. Operates natively on numpy arrays with no conversion overhead.
- Existing `libs/indicators/`: All new indicators (volume profile, Keltner channels, VWAP approximations) should be added here as pure-numpy functions, not via any external TA library.

`numba` is deferred: the current 500-sample workload is not compute-bound, and adding 200MB LLVM overhead to Docker is not warranted until profiling confirms a bottleneck at 9 strategies x 5 instruments. `ta-lib` should be removed from `pyproject.toml` — it is declared as a dependency but never imported, and its removal simplifies Docker builds and saves ~50MB. scikit-learn and xgboost should move to an optional `[ml]` extra group; they are out of scope for this milestone.

### Expected Features

See `.planning/research/FEATURES.md` for full detail with complexity assessments and data availability analysis.

**Must have (table stakes):**
- TS-1: Per-instrument parameter tuning — pure config change, highest ROI, zero risk
- TS-2: Adaptive conviction thresholds based on volatility regime — shared utility, cross-cutting
- TS-3: Funding rate arbitrage strategy — data ready, slot reserved, proven edge (~19% annual per Gate.io research)
- TS-4: Volume confirmation for momentum — data available and unused, eliminates weak crossover signals
- TS-5: Trend-aware filtering for mean reversion — multi-factor trend rejection vs current single ADX threshold
- TS-6: Dynamic stop placement — structure-aware stops vs fixed ATR multiples
- TS-7: Portfolio A routing for all strategies — high-conviction signals from any strategy should be eligible for autonomous execution

**Should have (competitive differentiators):**
- D-1: Orderbook imbalance strategy (basic threshold version) — slot reserved, data available
- D-4: Multi-window basis analysis for correlation strategy — three-factor model with funding integration
- D-5: Graduated liquidation cascade response — tiered OI drop tiers vs binary trigger
- D-6: Cross-strategy signal quality scoring — conviction normalization across strategies
- D-7: Time-of-day/session awareness — session classifier utility for all strategies

**Defer (requires data pipeline changes or feasibility concerns):**
- D-2: VWAP deviation strategy — 24h rolling volume vs per-bar volume is a data limitation; needs feasibility check before committing
- D-3: Volume profile strategy — true volume profile requires per-bar volume which is not in the current data pipeline

### Architecture Approach

The existing architecture requires almost no modification. All strategy work is confined to `agents/signals/strategies/` (new/modified strategy files), `agents/signals/feature_store.py` (add `timestamps` public property — 3 lines), `libs/common/models/enums.py` (add `VWAP` and `VOLUME_PROFILE` to `SignalSource` — 2 lines), `agents/signals/main.py` (add entries to `STRATEGY_CLASSES` dict — 1 line per strategy), and `configs/strategies/` (YAML files). Nothing downstream changes: alpha combiner, risk agent, execution, and reconciliation are untouched.

**Major components:**
1. `FeatureStore` — per-instrument rolling buffer of price, volume, OI, funding, and orderbook data; generic data provider for all strategies
2. `SignalStrategy` subclasses — stateless evaluation functions of `(snapshot, store) -> list[StandardSignal]`; each instrument gets its own strategy instances with per-instrument config already merged
3. Alpha Combiner — downstream consumer; handles signal deduplication, regime boosts, and portfolio routing; not modified in this milestone but its agreement-boost logic is a pitfall risk

**Key patterns to follow:** typed params dataclass per strategy, per-instrument cooldown tracking (dict-keyed by instrument, not a single counter), continuous conviction score (not binary), and `min_history` override for indicator warm-up periods.

### Critical Pitfalls

1. **Signal correlation cascade** — Momentum, regime_trend, and correlation strategies share overlapping price inputs. During trends, all fire LONG simultaneously and the alpha combiner amplifies the apparent consensus. Prevent by adding a rolling signal correlation tracker to the combiner and capping the agreement boost when source correlation exceeds 0.7. Address before adding more strategies.

2. **Overfitting per-instrument parameters** — With 5 instruments x ~10 params x 5 strategies, degrees of freedom vastly exceed observable signals. Constrain to 2-3 structurally justified params per instrument (e.g., ATR multiplier for a high-vol instrument), require documented rationale for every override, and enforce a minimum 2-market-cycle observation period before declaring parameters stable.

3. **Funding rate strategy trading already-priced-in information** — Funding rates at 0.01%/hour are smaller than taker fees for typical positions. A naive "go short when funding is positive" strategy loses money on entry/exit costs. Use funding rate as a conviction filter for other strategies (boost SHORT conviction when funding is extremely positive) rather than as a primary signal. Fix the paper simulator funding bug before any evaluation.

4. **Look-ahead bias in indicator computation** — Strategies compute indicators over `closes[-1]` which is a mid-candle sample, not a confirmed close. Crossovers detected this way can reverse by the next sample. Use `closes[-2]` as the "current" signal bar, and require a 1-bar persistence check for crossover signals.

5. **Shared cooldown state across instruments** — The `_bars_since_signal` counter in existing strategies is a single integer on the strategy instance. If any strategy is instantiated once globally (not per-instrument), an ETH signal suppresses a BTC signal. Verify instantiation pattern and convert to `dict[str, int]` keyed by instrument ID as a defensive fix.

## Implications for Roadmap

### Phase 1: Foundation and Quick Wins
**Rationale:** Per-instrument config tuning and existing strategy improvements require zero or minimal code changes but deliver immediate impact across all 5 instruments. Infrastructure prerequisites (FeatureStore timestamps, enum extensions, paper sim fix) are trivial changes that unblock everything else.
**Delivers:** Immediately smarter existing strategies; working paper trading baseline; config infrastructure for all subsequent phases.
**Addresses:** TS-1 (per-instrument tuning), TS-4 (volume confirmation for momentum), TS-5 (trend-aware filtering for mean reversion), Pitfall 10 (cooldown bug), Pitfall 12 (paper sim funding bug), Pitfall 13 (config schema validation).
**Avoids:** Overfitting pitfall by establishing tuning discipline before touching parameters.
**Research flag:** Standard patterns — skip phase research.

### Phase 2: New Strategies on Existing Data
**Rationale:** Funding arb and orderbook imbalance have pre-existing enum slots, config YAML files, routing rules, and all required data already in the FeatureStore. They are the lowest-risk new strategies to add.
**Delivers:** Two new signal sources; Portfolio A routing for time-sensitive signals; initial multi-strategy signal diversity.
**Addresses:** TS-3 (funding rate arb), D-1 (orderbook imbalance basic), Pitfall 4 (funding arb design discipline), Pitfall 8 (thin-book noise filtering for orderbook strategy).
**Uses:** scipy for funding rate z-score computation; bottleneck for rolling window statistics.
**Avoids:** Building funding strategy as primary signal rather than filter.
**Research flag:** Funding rate strategy mechanics are well-documented; orderbook imbalance threshold calibration may benefit from phase research given Coinbase INTX liquidity specifics.

### Phase 3: Strategy Improvements and Adaptive Behavior
**Rationale:** With all strategies running and producing data, adaptive improvements can be calibrated against observed signal distributions. TS-2 (adaptive conviction) is a shared utility that benefits all strategies and should be built after strategies are stable. Enhancement of existing strategies (correlation, liquidation cascade) builds on working code.
**Delivers:** Adaptive conviction scaling with volatility regime; multi-window correlation with funding integration; graduated liquidation cascade response; session-aware parameter adjustment.
**Addresses:** TS-2 (adaptive conviction), D-4 (multi-window basis analysis), D-5 (graduated cascade), D-7 (time-of-day awareness), Pitfall 5 (weekend regime blindness).
**Uses:** scipy percentile scoring for volatility regime detection; bottleneck rolling stats.
**Research flag:** Adaptive parameter frameworks have well-documented patterns. Session classification for crypto vs equity perps may benefit from brief phase research on Coinbase INTX equity perp trading hour conventions.

### Phase 4: Cross-Cutting Quality and Portfolio Routing
**Rationale:** Conviction normalization and Portfolio A routing across all strategies require all strategies to be in place and generating data. Signal correlation analysis (the most dangerous pitfall) requires multiple strategies running to measure.
**Delivers:** Normalized conviction scores across all strategies; Portfolio A routing for high-conviction signals from any strategy; signal correlation tracking in the alpha combiner; dynamic stop placement.
**Addresses:** TS-6 (dynamic stops), TS-7 (Portfolio A routing for all strategies), D-6 (cross-strategy conviction scoring), Pitfall 1 (signal correlation cascade), Pitfall 6 (conviction inflation), Pitfall 9 (adverse selection on Portfolio A trades).
**Research flag:** Conviction normalization and signal correlation tracking are specialized; phase research recommended to find established approaches from the quant literature.

### Phase 5: VWAP and Deferred Features (Conditional)
**Rationale:** VWAP strategy and volume profile are conditional on resolving the 24h rolling volume vs per-bar volume data limitation. These cannot be implemented correctly without either validating a volume-delta approximation or adding per-bar volume to the ingestion pipeline. Gate this phase on a feasibility decision from Phase 2 experimentation.
**Delivers:** VWAP deviation signals (if volume approximation is validated) or a data pipeline enhancement decision; FeatureStore multi-timeframe extension for medium-term strategies.
**Addresses:** D-2 (VWAP deviation), D-3 (volume profile if per-bar volume added), Pitfall 7 (FeatureStore horizon for new strategies), Pitfall 11 (volume data staleness).
**Research flag:** Phase research recommended for FeatureStore multi-timeframe extension design — this is an architectural change, not a simple addition.

### Phase Ordering Rationale

- **Config first:** Per-instrument tuning delivers immediate value with zero risk of breaking existing behavior. It also establishes the tuning discipline (documented rationale, minimal parameter overrides) before any code changes.
- **Infrastructure before strategies:** The paper simulator funding bug and per-instrument cooldown fix are prerequisites for valid measurement. Fixing them in Phase 1 means all subsequent phases are evaluated against accurate data.
- **Pre-wired strategies before net-new:** Funding arb and orderbook imbalance have more scaffolding than any other new strategy (existing enum slots, config files, routing rules). They are the lowest-risk path to expanding signal diversity.
- **Adaptive behavior after baseline:** Adaptive conviction, session awareness, and graduated responses are only calibratable once you have enough signal history. Building them before the baseline strategies are running means tuning against insufficient data.
- **Quality last, not least:** Signal correlation tracking and conviction normalization are the hardest to get right and the most impactful when multiple strategies are running. Deferring them to Phase 4 means they can be calibrated against real multi-strategy signal data.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (orderbook imbalance):** Coinbase INTX-specific liquidity and book depth characteristics may require empirical calibration; worth a brief research pass on INTX market microstructure before setting threshold defaults.
- **Phase 4 (signal correlation and conviction normalization):** Specialized quant topic with limited publicly documented implementations; phase research recommended.
- **Phase 5 (FeatureStore multi-timeframe extension):** Architectural change to a core shared component; warrants design research before implementation.

Phases with standard patterns (skip research-phase):
- **Phase 1 (config tuning and existing strategy improvements):** Well-established patterns, all based on direct code analysis.
- **Phase 2 (funding arb):** Funding rate mechanics are thoroughly documented; strategy design is clear from FEATURES.md and PITFALLS.md.
- **Phase 3 (adaptive conviction, session awareness):** Volatility percentile and session classification patterns are standard quant toolkit.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Based on direct codebase analysis; scipy and bottleneck are undisputed choices for the specific operations needed |
| Features | HIGH | Features directly derived from existing code gaps and PROJECT.md stated goals; differentiators validated against academic and practitioner sources |
| Architecture | HIGH | Primary source is direct codebase analysis; all claims verified against actual source files |
| Pitfalls | HIGH | All critical pitfalls derived from actual code analysis of existing strategies, FeatureStore, and alpha combiner; not speculative |

**Overall confidence:** HIGH

### Gaps to Address

- **VWAP volume approximation validity:** The 24h rolling volume limitation means VWAP computation would use volume deltas between samples, not true per-bar volume. Whether this approximation is reliable enough for a trading signal requires empirical testing. Gate the VWAP strategy on a Phase 2 feasibility experiment: compute approximated VWAP and compare against exchange-provided VWAP if available.
- **Orderbook imbalance autocorrelation threshold:** The research recommends requiring lag-1 autocorrelation >0.3 before trusting the orderbook imbalance metric as a primary signal. This threshold needs empirical calibration against actual Coinbase INTX imbalance data — the right threshold may differ by instrument.
- **Paper simulator accuracy for multi-hour holding strategies:** The funding settlement bug is documented but the fix complexity is unknown. If the fix requires changes to the execution simulation model, it could be more involved than a minor patch. Scope this before Phase 1 completes.
- **Alpha combiner source code availability:** The ARCHITECTURE.md notes the alpha combiner is "untouched" but Phase 4 requires modifying it (signal correlation tracker, conviction budget). Confirm the combiner is within scope for this milestone before scheduling Phase 4 work.

## Sources

### Primary (HIGH confidence)

- Direct codebase analysis — `agents/signals/strategies/`, `agents/signals/feature_store.py`, `agents/signals/main.py`, `libs/common/config.py`, `libs/common/models/enums.py`, `libs/common/models/signal.py`, `libs/indicators/`, `configs/` — all architectural and pitfall findings
- `configs/default.yaml` — routing rules, fee structure, risk limits, execution parameters
- PROJECT.md — milestone scope, explicit out-of-scope items (ML, on-chain data, cross-exchange arb)
- CONCERNS.md — known paper simulator bugs, data staleness issues

### Secondary (MEDIUM confidence)

- [Gate.io: Perpetual Contract Funding Rate Arbitrage 2025](https://www.gate.com/learn/articles/perpetual-contract-funding-rate-arbitrage/2166) — 19.26% annual return claim for funding arb
- [MDPI 2025: Adaptive Optimization of Dual Moving Average Strategy](https://www.mdpi.com/2227-7390/13/16/2629) — per-instrument adaptive parameters research
- [Towards Data Science: Price Impact of Order Book Imbalance in Crypto](https://towardsdatascience.com/price-impact-of-order-book-imbalance-in-cryptocurrency-markets-bf39695246f6/) — OBI predictive power evidence
- [arXiv: Systematic Trend-Following with Adaptive Portfolio Construction](https://arxiv.org/html/2602.11708v1) — trailing stops and regime adaptation
- [Bottleneck GitHub](https://github.com/pydata/bottleneck) — rolling operation benchmark data
- [SciPy documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.zscore.html) — API verification

### Tertiary (LOW confidence)

- [arXiv: Deep Learning for VWAP Execution in Crypto Markets](https://arxiv.org/html/2502.13722v1) — VWAP conventions in crypto perpetuals (confirms midnight UTC reset convention but uses ML-based execution, not directly applicable)
- [Medium: Multi-Timeframe Adaptive Market Regime Strategy](https://medium.com/@FMZQuant/multi-timeframe-adaptive-market-regime-quantitative-trading-strategy-1b16309ddabb) — regime-based parameter adaptation patterns

---
*Research completed: 2026-03-21*
*Ready for roadmap: yes*
