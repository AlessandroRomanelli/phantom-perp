# Requirements: Phantom Perp — Forensic Audit Fixes

**Defined:** 2026-04-08
**Core Value:** Fix structural profitability issues — eliminate bugs, recalibrate sizing/execution, fix corrupted data

## v1.4 Requirements

Requirements derived from 5-agent forensic profitability audit (composite score 4.3/10).

### Safety Critical (Tier 1)

- [ ] **SAFE-01**: Paper mode executes each order exactly once (eliminate double execution from PaperBroker + paper_simulator consuming same stream)
- [ ] **SAFE-02**: Risk agent daily loss and drawdown kill switches use cumulative realized P&L (not hardcoded zeros) in both paper and live mode
- [ ] **SAFE-03**: RankedTradeIdea metadata (including funding_rate) survives serialization through Redis so the funding rate circuit breaker functions
- [ ] **SAFE-04**: MAX_LEVERAGE_GLOBAL is restored to Decimal("5.0") matching the documented safety specification
- [ ] **SAFE-05**: Reconciliation agent uses Route B API credentials when polling Route B portfolio state (not Route A credentials for both)

### Profitability (Tier 2)

- [ ] **PROF-01**: Position sizing conviction_power is reduced so strategies with 0.4-0.6 conviction produce positions large enough to overcome round-trip fees
- [ ] **PROF-02**: Stop-loss protective orders use STOP_LIMIT (maker fee) instead of STOP_MARKET (taker fee) with a configurable limit buffer
- [ ] **PROF-03**: Feature store bar_volumes provides true per-bar volume computed from candle data, not 24h cumulative window diffs
- [ ] **PROF-04**: ADX indicator uses `np.isnan()` for NaN checks instead of identity comparison, producing correct trend strength values
- [ ] **PROF-05**: Risk engine rejects trades where estimated round-trip fees exceed expected edge (fee-adjusted signal filter)

### Robustness (Tier 3)

- [ ] **ROBU-01**: Risk engine checks net directional exposure across correlated instruments and rejects trades that would create concentrated directional bets
- [ ] **ROBU-02**: Bollinger Bands use sample standard deviation (ddof=1) consistently with other volatility calculations
- [ ] **ROBU-03**: Paper simulator models probabilistic fills (not 100% instant) with adverse selection and SL slippage
- [ ] **ROBU-04**: Risk agent tracks equity high-water mark for true peak-to-trough drawdown protection (not daily loss proxy)
- [ ] **ROBU-05**: MarketSnapshot index_price is sourced from exchange data or basis-dependent strategies are disabled when index is unavailable
- [ ] **ROBU-06**: BTC-PERP has a higher max_position_notional_usdc or OBI has a longer cooldown to reduce fee-negative high-frequency trading

## Validated (Previous Milestones)

- v1.3: BUG-01 through BUG-04 (deserialization, dedup, reduce_only, positions) — all fixed
- v1.3: INFR-01 (PEL reclaim via XAUTOCLAIM) — fixed
- v1.3: TEST-01 through TEST-04 (messaging, router, risk, indicator tests) — all covered
- v1.2: DATA-01 through CLAI-04 (PostgreSQL, metrics, safety, Claude integration) — all shipped
- v1.1: Multi-instrument ingestion, Coinbase Advanced Trade migration — shipped
- v1.0: Strategy enhancement, per-instrument tuning — shipped

## Future Requirements

Deferred to a later milestone.

- **SEC-01**: Redis AUTH configuration with password authentication
- **SEC-02**: Dashboard agent exception handlers replaced with structured logging
- **EXEC-01**: Adaptive limit offset based on spread width and volatility regime
- **EXEC-02**: Order TTL enforcement in paper mode (cancel unfilled orders after timeout)
- **EXEC-03**: Slippage enforcement at execution time (max_slippage_bps check on fills)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Strategy rewrite or replacement | v1.4 fixes infrastructure; strategy alpha improvements deferred |
| Scorecard weight tuning | Needs accumulated trade data first; scorecard adjusts automatically |
| Backtesting framework | Separate project |
| New instrument onboarding | Not needed for profitability fixes |
| Real-time trailing stops | Adds complexity; static SL/TP with correct R:R is sufficient for now |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SAFE-01 | Phase 21 | Pending |
| SAFE-02 | Phase 21 | Pending |
| SAFE-03 | Phase 21 | Pending |
| SAFE-04 | Phase 21 | Pending |
| SAFE-05 | Phase 21 | Pending |
| PROF-01 | Phase 23 | Pending |
| PROF-02 | Phase 23 | Pending |
| PROF-03 | Phase 22 | Pending |
| PROF-04 | Phase 22 | Pending |
| PROF-05 | Phase 23 | Pending |
| ROBU-01 | Phase 24 | Pending |
| ROBU-02 | Phase 22 | Pending |
| ROBU-03 | Phase 25 | Pending |
| ROBU-04 | Phase 24 | Pending |
| ROBU-05 | Phase 22 | Pending |
| ROBU-06 | Phase 23 | Pending |

**Coverage:**
- v1.4 requirements: 16 total
- Mapped to phases: 16
- Unmapped: 0

---
*Requirements defined: 2026-04-08*
*Source: 5-agent forensic profitability audit (reports/forensic-audit.md)*
