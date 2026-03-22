# Roadmap: Phantom Perp Strategy Enhancement

## Overview

This milestone transforms Phantom Perp from a conservative, one-size-fits-all signal bot into an instrument-aware, multi-strategy system that trades smarter and more often. The work starts with infrastructure prerequisites and per-instrument tuning (zero-risk, highest ROI), then improves the five existing strategies in two waves, adds three new strategies (funding rate filter, orderbook imbalance, VWAP deviation), and finishes with cross-cutting quality work that normalizes conviction and enables session-aware parameter selection across all strategies.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation and Per-Instrument Tuning** - Infrastructure prereqs, bug fixes, and per-instrument YAML configs for all 5 instruments
- [x] **Phase 2: Momentum and Mean Reversion Improvements** - Volume confirmation, adaptive conviction, trend-aware filtering, dynamic bands, and Portfolio A routing for the two core strategies (completed 2026-03-22)
- [x] **Phase 3: Liquidation, Correlation, and Regime Improvements** - Graduated cascade response, multi-window basis analysis, funding integration, adaptive regime filters, and Portfolio A routing (completed 2026-03-22)
- [ ] **Phase 4: New Strategies** - Funding rate filter, orderbook imbalance strategy, and VWAP deviation strategy as new signal sources
- [ ] **Phase 5: Cross-Cutting Quality** - Adaptive conviction utility, session classification, conviction normalization, and dynamic stop placement across all strategies

## Phase Details

### Phase 1: Foundation and Per-Instrument Tuning
**Goal**: The system has all infrastructure prerequisites in place and every instrument trades with asset-appropriate thresholds instead of universal defaults
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06, INFRA-07, TUNE-01, TUNE-02, TUNE-03, TUNE-04, TUNE-05
**Success Criteria** (what must be TRUE):
  1. Each of the 5 instruments (ETH, BTC, SOL, QQQ, SPY) loads its own parameter overrides from YAML config, and the startup log shows which parameters differ from defaults
  2. Strategy cooldown state is tracked per-instrument so an ETH signal does not suppress a BTC signal
  3. Unknown YAML parameter keys produce a warning at startup rather than being silently ignored
  4. FeatureStore exposes a timestamps accessor and computes bar_volume deltas, and the SignalSource enum includes VWAP and VOLUME_PROFILE entries
  5. scipy and bottleneck are available as dependencies for statistical computations
**Plans**: 3 plans

Plans:
- [ ] 01-01-PLAN.md — Infrastructure: dependencies, enum entries, FeatureStore extensions, cooldown verification
- [ ] 01-02-PLAN.md — Config validation and diff logging
- [ ] 01-03-PLAN.md — Strategy matrix and per-instrument parameter tuning

### Phase 2: Momentum and Mean Reversion Improvements
**Goal**: The two highest-frequency existing strategies produce higher-quality signals with fewer false positives and can route high-conviction signals to Portfolio A
**Depends on**: Phase 1
**Requirements**: MOM-01, MOM-02, MOM-03, MOM-04, MR-01, MR-02, MR-03, MR-04
**Success Criteria** (what must be TRUE):
  1. Momentum strategy rejects EMA crossovers when volume rate-of-change is declining, reducing false breakout signals
  2. Momentum conviction scales with current vs historical volatility percentile rather than using fixed thresholds
  3. Momentum and mean reversion strategies place stops at recent swing highs/lows (or partial targets at mean) instead of fixed ATR multiples
  4. Mean reversion strategy uses multi-factor trend rejection (EMA slope + consecutive closes + momentum strength) instead of a single ADX threshold
  5. High-conviction signals from both strategies route to Portfolio A for autonomous execution
**Plans**: 2 plans

Plans:
- [ ] 02-01-PLAN.md — Momentum: volume confirmation, adaptive conviction, swing stops, Portfolio A routing, YAML loader fix
- [ ] 02-02-PLAN.md — Mean reversion: multi-factor trend rejection, adaptive bands, extended targets, volume boost, Portfolio A routing

### Phase 3: Liquidation, Correlation, and Regime Improvements
**Goal**: The remaining three existing strategies produce more nuanced signals with graduated responses, multi-window analysis, and adaptive thresholds
**Depends on**: Phase 1
**Requirements**: LIQ-01, LIQ-02, CORR-01, CORR-02, CORR-03, RT-01, RT-02
**Success Criteria** (what must be TRUE):
  1. Liquidation cascade strategy responds with graduated tiers (mild/moderate/severe OI drops) with different position sizing and stop widths per tier
  2. Liquidation cascade requires volume surge confirmation alongside OI drops to distinguish forced liquidation from organic reduction
  3. Correlation strategy uses short/medium/long lookback windows and fires when multiple windows agree, with funding rate as a third factor boosting conviction
  4. Regime trend strategy adjusts ADX and ATR expansion thresholds based on volatility regime, and emits tighter initial stops with trail parameter metadata
  5. High-conviction correlation signals route to Portfolio A for autonomous execution
**Plans**: 3 plans

Plans:
- [ ] 03-01-PLAN.md — Liquidation cascade: graduated tiers with volume surge confirmation
- [ ] 03-02-PLAN.md — Correlation: multi-window basis analysis, funding rate integration, Portfolio A routing
- [ ] 03-03-PLAN.md — Regime trend: adaptive ADX/ATR thresholds, trailing stop metadata

### Phase 4: New Strategies
**Goal**: Three new signal sources fill coverage gaps in funding rate dislocations, orderbook flow, and intraday VWAP deviation
**Depends on**: Phase 1
**Requirements**: FUND-01, FUND-02, FUND-03, OBI-01, OBI-02, OBI-03, OBI-04, VWAP-01, VWAP-02, VWAP-03, VWAP-04
**Success Criteria** (what must be TRUE):
  1. Funding rate filter boosts conviction for directional signals aligned with extreme funding (SHORT when funding extreme positive, LONG when extreme negative) using rolling z-scores with time-to-settlement decay
  2. Orderbook imbalance strategy emits directional signals based on time-weighted bid/ask depth imbalance, suppressing signals when the book is too thin
  3. Orderbook imbalance signals route to Portfolio A given their short time horizon
  4. VWAP feasibility is validated: either the volume-delta approximation produces usable VWAP values and the strategy emits deviation-based signals with session reset and time-of-session awareness, or the strategy is deferred to v2 with documented rationale
**Plans**: 3 plans

Plans:
- [ ] 04-01-PLAN.md — Funding rate filter utility: z-score computation, time-to-settlement decay, integration into correlation/momentum/mean_reversion
- [ ] 04-02-PLAN.md — Orderbook imbalance strategy: time-weighted imbalance, depth gate, Portfolio A routing, per-instrument config
- [ ] 04-03-PLAN.md — VWAP feasibility validation and conditional strategy implementation or deferral

### Phase 5: Cross-Cutting Quality
**Goal**: All strategies benefit from shared utilities for adaptive conviction, session awareness, conviction normalization, and structure-aware stops
**Depends on**: Phase 2, Phase 3, Phase 4
**Requirements**: XQ-01, XQ-02, XQ-03, XQ-04, XQ-05
**Success Criteria** (what must be TRUE):
  1. A shared adaptive conviction utility scales min_conviction thresholds with volatility percentile, usable by any strategy
  2. A session/time-of-week classifier distinguishes crypto_weekday, crypto_weekend, equity_market_hours, and equity_off_hours, and strategies load different thresholds based on the current session
  3. Conviction bands (low/medium/high) are defined and all strategies map their raw conviction to consistent normalized values
  4. A shared swing-point detection utility provides structure-aware stop placement, reusable across momentum, mean reversion, and regime trend strategies
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order. Phases 2, 3, and 4 depend only on Phase 1 and could theoretically overlap, but execute sequentially: 1 -> 2 -> 3 -> 4 -> 5.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation and Per-Instrument Tuning | 2/3 | In Progress|  |
| 2. Momentum and Mean Reversion Improvements | 2/2 | Complete   | 2026-03-22 |
| 3. Liquidation, Correlation, and Regime Improvements | 3/3 | Complete   | 2026-03-22 |
| 4. New Strategies | 1/3 | In Progress|  |
| 5. Cross-Cutting Quality | 0/? | Not started | - |
