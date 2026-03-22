# Phantom Perp — Strategy Enhancement

## What This Is

A trading strategy improvement project for the Phantom Perp system — an event-driven, multi-agent perpetual futures trading bot on Coinbase INTX. The goal is to make the existing 5 strategies smarter and more active, add proven new strategies that fill signal gaps, and tune everything per-instrument across 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY).

## Core Value

Better signal quality and broader market coverage — the bot should trade smarter when it fires and fire more often by capturing opportunities the current strategies miss (low-vol periods, funding rate dislocations, orderbook flow, volume-based entries).

## Requirements

### Validated

- ✓ Momentum strategy (EMA crossover + ADX + RSI) — existing
- ✓ Mean reversion strategy (Bollinger Band + RSI + ADX inverse) — existing
- ✓ Liquidation cascade strategy (OI drop + orderbook imbalance) — existing
- ✓ Correlation strategy (basis divergence + OI/price divergence) — existing
- ✓ Regime trend strategy (triple filter + breakout/pullback) — existing
- ✓ Dual portfolio architecture (A=autonomous, B=Telegram-confirmed) — existing
- ✓ Per-strategy YAML config with parameter overrides — existing
- ✓ FeatureStore with price, volume, OI, funding, orderbook data — existing
- ✓ StandardSignal contract with conviction, direction, stops — existing
- ✓ SignalSource enum with slots for funding_arb, orderbook_imbalance — existing
- ✓ Per-instrument parameter tuning — separate configs for ETH, BTC, SOL, QQQ, SPY with asset-appropriate thresholds — Validated in Phase 1
- ✓ Config schema validation — unknown YAML keys detected at startup — Validated in Phase 1
- ✓ FeatureStore timestamps and bar_volume deltas — Validated in Phase 1
- ✓ SignalSource enum includes VWAP and VOLUME_PROFILE entries — Validated in Phase 1
- ✓ Strategy matrix for per-instrument enablement — Validated in Phase 1
- ✓ Momentum strategy — volume confirmation, adaptive conviction, swing stops, Portfolio A routing — Validated in Phase 2
- ✓ Mean reversion strategy — multi-factor trend rejection, adaptive bands, extended targets, Portfolio A routing — Validated in Phase 2

### Active

- [ ] Improve liquidation cascade strategy — graduated response levels, volume confirmation, better timing
- [ ] Improve correlation strategy — multi-window basis analysis, funding rate integration, stronger divergence detection
- [ ] Improve regime trend strategy — adaptive filter thresholds, multi-timeframe confirmation, trailing stops
- [ ] Funding rate arbitrage strategy — trade funding rate dislocations, carry trade logic, predicted vs actual rate divergence
- [ ] Orderbook imbalance strategy — bid/ask depth analysis, absorption detection, sweep detection
- [ ] VWAP strategy — deviation from session VWAP as mean reversion anchor, time-of-day awareness
- [ ] Volume profile strategy — high-volume nodes as support/resistance, low-volume gaps as breakout targets
- [ ] Dual portfolio routing for all strategies — high-conviction fast signals → Portfolio A, all else → Portfolio B
- [ ] Cross-strategy signal quality — conviction models that account for instrument-specific volatility and liquidity

### Out of Scope

- On-chain data integration (ONCHAIN signal source) — requires external data providers, deferred
- Sentiment analysis (SENTIMENT signal source) — requires NLP/social data pipeline, deferred
- New instrument onboarding — current 5 instruments are sufficient for this milestone
- Execution layer changes — strategy layer only; execution, risk, and alpha combiner are untouched
- Backtesting framework — would be valuable but is a separate project
- Machine learning / adaptive parameter optimization — stick to proven quant patterns for now

## Context

- System is brownfield: full pipeline exists from ingestion → signals → alpha → risk → execution → reconciliation
- Paper trading on ETH-PERP showed very low activity, especially on weekends — thresholds are too conservative for current market conditions
- Strategies all follow the same `SignalStrategy` base class pattern with `evaluate(snapshot, store) → list[StandardSignal]`
- FeatureStore samples at 60-second intervals, holds up to 500 samples (~8 hours of data)
- Volume and funding rate data are already collected but underutilized by current strategies
- The alpha combiner handles signal aggregation, regime detection, and conflict resolution — strategies just emit raw signals
- Crypto perps (ETH/BTC/SOL) trade 24/7 with hourly funding; equity perps (QQQ/SPY) have different liquidity patterns
- Per-instrument YAML overrides already supported in `configs/strategies/<strategy>.yaml`

## Constraints

- **Architecture**: Must use existing `SignalStrategy` base class and `StandardSignal` contract — no changes to the signal interface
- **Data**: Limited to data already in MarketSnapshot and FeatureStore — no new data sources or API integrations
- **Risk**: Strategy changes must not weaken existing risk guardrails — risk agent and limits are untouched
- **Config**: All new parameters must be configurable via YAML in `configs/strategies/` — no hardcoded magic numbers
- **Routing**: Portfolio A routing requires `suggested_target=PortfolioTarget.A` with appropriate conviction thresholds per strategy
- **Instruments**: Per-instrument configs go in existing `configs/strategies/<strategy>.yaml` under `instruments:` key

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Per-instrument tuning over universal params | ETH weekends vs SPY equity hours are completely different — one-size-fits-all leaves performance on the table | ✓ Phase 1 |
| Proven quant patterns over exotic signals | Focus on well-established patterns (VWAP, volume profile, funding arb, orderbook flow) before exploring exotic approaches | — Pending |
| Dual routing for all strategies | High-conviction signals from any strategy should be eligible for Portfolio A autonomous execution | — Pending |
| Both improve existing + build new | Equal priority on making current strategies smarter and adding new ones that fill coverage gaps | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-22 after Phase 2 completion*
