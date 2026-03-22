# Phantom Perp — Strategy Enhancement

## What This Is

A multi-strategy perpetual futures trading system on Coinbase INTX with 7 signal strategies, per-instrument tuning across 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY), session-aware parameter selection, and dual portfolio routing (autonomous + Telegram-confirmed).

## Core Value

Better signal quality and broader market coverage — the bot trades smarter with volume-confirmed, volatility-adaptive signals and fires more often by capturing opportunities across funding rate dislocations, orderbook flow, and VWAP deviations.

## Current Milestone: v1.1 Multi-Instrument Ingestion

**Goal:** Enable all 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY) to flow through the ingestion pipeline so that the v1.0 strategy improvements evaluate across all instruments, not just ETH-PERP.

**Target features:**
- Multi-instrument WebSocket subscription (single connection, multiple products)
- Per-instrument candle and funding rate polling
- Multi-instrument config structure in default.yaml
- Per-instrument IngestionState management
- End-to-end verification: snapshots for all 5 instruments reach the signals agent

## Current State (Phase 6 complete 2026-03-22)

- **7 strategies**: momentum, mean reversion, liquidation cascade, correlation, regime trend, orderbook imbalance, VWAP deviation
- **Shared utilities**: funding rate filter, adaptive conviction, swing points, session classifier, conviction normalizer
- **Per-instrument tuning**: all 5 instruments have asset-specific thresholds across all strategies
- **Session awareness**: crypto_weekday/weekend, equity_market/off_hours with separate configs
- **Portfolio A routing**: unified conviction threshold at 0.70 via conviction normalizer
- **Config infrastructure**: strategy matrix, schema validation, startup diff logging, InstrumentConfig registry
- **Instrument registry**: config-driven InstrumentConfig with per-instrument specs (tick size, lot size, max leverage) — zero hardcoded instrument constants
- **Test suite**: 743 tests, all passing
- **Codebase**: ~13k LOC Python in signals/libs

## Requirements

### Validated

- ✓ Per-instrument parameter tuning for ETH, BTC, SOL, QQQ, SPY — v1.0
- ✓ Config schema validation and diff logging — v1.0
- ✓ FeatureStore extensions (timestamps, bar_volumes) — v1.0
- ✓ Strategy matrix for per-instrument enablement — v1.0
- ✓ Momentum: volume confirmation, adaptive conviction, swing stops, Portfolio A — v1.0
- ✓ Mean reversion: multi-factor trend rejection, adaptive bands, extended targets, Portfolio A — v1.0
- ✓ Liquidation cascade: graduated tiers, volume surge confirmation — v1.0
- ✓ Correlation: multi-window basis, funding rate integration, Portfolio A — v1.0
- ✓ Regime trend: adaptive ADX/ATR thresholds, trailing stop metadata — v1.0
- ✓ Funding rate filter utility with z-score and time-to-settlement decay — v1.0
- ✓ Orderbook imbalance strategy with depth gate, Portfolio A — v1.0
- ✓ VWAP deviation strategy with session-aware reset — v1.0
- ✓ Cross-strategy conviction normalization with unified bands — v1.0
- ✓ Session-aware parameter selection — v1.0
- ✓ Shared adaptive conviction and swing point utilities — v1.0
- ✓ Multi-instrument config in default.yaml — instruments list with 5 contracts. Validated in Phase 6: config-state-foundation
- ✓ Per-instrument IngestionState management — Dict[str, IngestionState] keyed by instrument. Validated in Phase 6: config-state-foundation
- ✓ Remove hardcoded INSTRUMENT_ID references from ingestion layer — Validated in Phase 6: config-state-foundation

### Active

- [ ] Multi-instrument WebSocket ingestion — subscribe to all 5 perp contracts via single WS connection
- [ ] Multi-instrument candle polling — poll candles for each instrument separately
- [ ] Multi-instrument funding rate polling — poll funding for each instrument separately
- [ ] End-to-end multi-instrument verification — all 5 instruments produce snapshots that reach signals agent

### Deferred

- [ ] Volume profile strategy — high-volume nodes as support/resistance (deferred from v1.0 — requires per-bar volume ingestion)

### Out of Scope

- On-chain data integration — requires external data providers
- Sentiment analysis — requires NLP/social data pipeline
- New instrument onboarding — current 5 instruments are sufficient
- Execution layer changes — execution, risk, and alpha combiner are untouched
- Backtesting framework — valuable but separate project
- Machine learning / adaptive parameter optimization — proven quant patterns only
- Trailing stop state management in execution layer — metadata emitted but no consumer yet (ADV-02)

## Constraints

- **Architecture**: Uses existing `SignalStrategy` base class and `StandardSignal` contract
- **Data**: Uses Coinbase INTX API for all 5 perpetual contracts — no external data providers
- **Risk**: Risk guardrails untouched — risk agent and limits are unchanged
- **Config**: All parameters configurable via YAML — no hardcoded magic numbers
- **Routing**: Portfolio A routing via `suggested_target=PortfolioTarget.A` with unified 0.70 threshold
- **Instruments**: Per-instrument configs in `configs/strategies/<strategy>.yaml` under `instruments:` key

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Per-instrument tuning over universal params | ETH weekends vs SPY equity hours are completely different | ✓ Phase 1 |
| Proven quant patterns over exotic signals | VWAP, funding arb, orderbook flow before exotic approaches | ✓ Phase 4 |
| Dual routing for all strategies | High-conviction from any strategy eligible for Portfolio A | ✓ Phase 5 |
| Both improve existing + build new | Equal priority on smarter strategies and new coverage | ✓ Phases 2-4 |
| Funding filter as shared utility | Boost-only semantics, opt-in per strategy | ✓ Phase 4 |
| Post-processing conviction normalization | Don't rewrite internal models — overlay bands | ✓ Phase 5 |
| Session config in separate file | Clean separation from per-instrument YAML | ✓ Phase 5 |
| VWAP feasibility validated programmatically | bar_volumes clamped, 8x smoother than raw price | ✓ Phase 4 |
| Config-driven instrument registry over constants | Enables multi-instrument without code changes per new instrument | ✓ Phase 6 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-22 after Phase 6 completion*
