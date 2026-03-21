# Requirements: Phantom Perp Strategy Enhancement

**Defined:** 2026-03-21
**Core Value:** Better signal quality and broader market coverage across all instruments and conditions

## v1 Requirements

### Infrastructure & Bug Fixes

- [x] **INFRA-01**: Per-instrument cooldown tracking — strategy cooldown state must be keyed by instrument ID, not shared globally
- [ ] **INFRA-02**: Config schema validation — warn on unknown YAML parameter keys that don't match the strategy's params dataclass fields
- [ ] **INFRA-03**: Config diff logging at startup — log which per-instrument parameters differ from defaults and by how much
- [x] **INFRA-04**: Add `scipy` and `bottleneck` dependencies for statistical computations and fast rolling windows
- [x] **INFRA-05**: Add `VWAP` and `VOLUME_PROFILE` entries to `SignalSource` enum
- [x] **INFRA-06**: Add `timestamps` property accessor to FeatureStore (needed for VWAP session reset)
- [x] **INFRA-07**: Compute and store `bar_volume` deltas between consecutive volume_24h samples in FeatureStore

### Per-Instrument Parameter Tuning

- [ ] **TUNE-01**: ETH-PERP strategy config — asset-specific thresholds for all strategies reflecting ETH volatility and 24/7 trading
- [ ] **TUNE-02**: BTC-PERP strategy config — asset-specific thresholds reflecting BTC's higher liquidity and different volatility profile
- [ ] **TUNE-03**: SOL-PERP strategy config — asset-specific thresholds reflecting SOL's high volatility and thinner orderbook
- [ ] **TUNE-04**: QQQ-PERP strategy config — equity perp thresholds active primarily during US market hours
- [ ] **TUNE-05**: SPY-PERP strategy config — equity perp thresholds active primarily during US market hours

### Momentum Strategy Improvements

- [ ] **MOM-01**: Volume confirmation — reject EMA crossovers when volume rate-of-change is declining
- [ ] **MOM-02**: Adaptive conviction model — scale conviction thresholds with current vs historical volatility percentile
- [ ] **MOM-03**: Structure-aware stop placement — use recent swing high/low instead of fixed ATR multiples
- [ ] **MOM-04**: Portfolio A dual routing — high-conviction breakout signals eligible for autonomous execution

### Mean Reversion Strategy Improvements

- [ ] **MR-01**: Multi-factor trend rejection — EMA slope + consecutive closes + momentum strength, not just ADX threshold
- [ ] **MR-02**: Adaptive band width — adjust Bollinger Band std multiplier based on volatility regime
- [ ] **MR-03**: Improved take-profit targeting — partial targets at mean, extended targets beyond for strong reversions
- [ ] **MR-04**: Portfolio A dual routing — extreme deviation (3+ sigma) signals eligible for autonomous execution

### Liquidation Cascade Strategy Improvements

- [ ] **LIQ-01**: Graduated response levels — Tier 1 (mild OI drop 2-4%), Tier 2 (moderate 4-8%), Tier 3 (severe >8%) with different position sizing and stop widths
- [ ] **LIQ-02**: Volume surge confirmation — require volume spike alongside OI drop to distinguish forced liquidation from organic OI reduction

### Correlation Strategy Improvements

- [ ] **CORR-01**: Multi-window basis analysis — short (30 bars), medium (60 bars), long (120 bars) lookback windows; signal fires when multiple agree
- [ ] **CORR-02**: Funding rate integration — extreme funding + extreme basis = higher conviction; create three-factor model
- [ ] **CORR-03**: Portfolio A dual routing — multi-window + funding agreement signals eligible for autonomous execution

### Regime Trend Strategy Improvements

- [ ] **RT-01**: Adaptive filter thresholds — ADX and ATR expansion thresholds adjust with volatility regime
- [ ] **RT-02**: Dynamic trailing stop concept — emit tighter initial stop with metadata suggesting trail parameters for execution layer

### Funding Rate Filter

- [ ] **FUND-01**: Funding rate as confirmation filter — boost conviction for momentum-SHORT when funding is extreme positive, boost mean-reversion-LONG when funding is extreme negative
- [ ] **FUND-02**: Funding rate z-score computation — rolling z-score of funding rate vs historical distribution
- [ ] **FUND-03**: Time-to-funding decay — signal urgency increases as next funding settlement approaches

### Orderbook Imbalance Strategy

- [ ] **OBI-01**: New strategy using bid/ask depth imbalance as directional signal for short-term trades
- [ ] **OBI-02**: Time-weighted imbalance — average imbalance over multiple samples rather than point-in-time
- [ ] **OBI-03**: Minimum depth gate — suppress signals when orderbook is too thin to be meaningful
- [ ] **OBI-04**: Portfolio A routing — short time horizon signals route to autonomous execution

### VWAP Deviation Strategy

- [ ] **VWAP-01**: Feasibility validation — confirm volume-delta approximation from 24h rolling data produces usable VWAP values
- [ ] **VWAP-02**: Session VWAP computation — VWAP with configurable session reset (00:00 UTC for crypto, 09:30 ET for equity)
- [ ] **VWAP-03**: Deviation-based signals — extreme deviations from session VWAP as mean reversion triggers
- [ ] **VWAP-04**: Time-of-session awareness — VWAP signals more reliable later in session when VWAP has stabilized

### Cross-Cutting Quality

- [ ] **XQ-01**: Adaptive conviction thresholds — shared utility that scales min_conviction with volatility percentile for any strategy
- [ ] **XQ-02**: Session/time-of-week classifier — classify current time as crypto_weekday, crypto_weekend, equity_market_hours, equity_off_hours
- [ ] **XQ-03**: Session-aware parameter selection — strategies load different thresholds based on current session classification
- [ ] **XQ-04**: Cross-strategy conviction normalization — define conviction bands (low/medium/high) and ensure consistent mapping across all strategies
- [ ] **XQ-05**: Dynamic stop placement utility — swing point detection for structure-aware stops, reusable across strategies

## v2 Requirements

### Paper Simulator Fixes

- **PSIM-01**: Simulate hourly funding rate settlements in paper trading mode
- **PSIM-02**: Simulate partial fills and slippage for large orders in paper mode

### Alpha Combiner Improvements

- **ALPHA-01**: Signal correlation tracking — discount agreement boost when source strategies are highly correlated
- **ALPHA-02**: Conviction budget per time window — cap total conviction emitted to prevent simultaneous max-position entries
- **ALPHA-03**: Unique information requirement — only boost conviction when contributing strategies use genuinely different data

### Volume Profile Strategy

- **VPRO-01**: Per-bar volume ingestion in FeatureStore (requires data pipeline changes)
- **VPRO-02**: Price-volume histogram with HVN/LVN detection
- **VPRO-03**: Support/resistance signals from volume nodes

### Advanced Features

- **ADV-01**: Multi-timeframe FeatureStore — 60s + 5m + 15m buffers for longer-horizon strategies
- **ADV-02**: Trailing stop state management in execution layer
- **ADV-03**: Fill quality tracking for Portfolio A adverse selection monitoring

## Out of Scope

| Feature | Reason |
|---------|--------|
| Machine learning parameter optimization | Requires backtesting framework that doesn't exist; overfitting risk without walk-forward validation |
| Grid trading / DCA strategies | Wrong paradigm — not signal-based directional |
| Cross-exchange arbitrage | Requires multi-exchange connectivity, different infrastructure |
| HFT orderbook microstructure | FeatureStore samples at 60s — architecture is medium-frequency by design |
| On-chain / sentiment data | Requires external data providers and new ingestion pipelines |
| New instrument onboarding | Current 5 instruments are sufficient for this milestone |
| Execution layer changes | Strategy layer only — execution, risk, alpha combiner are untouched in v1 |
| Backtesting framework | Valuable but separate project |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Complete |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Complete |
| INFRA-05 | Phase 1 | Complete |
| INFRA-06 | Phase 1 | Complete |
| INFRA-07 | Phase 1 | Complete |
| TUNE-01 | Phase 1 | Pending |
| TUNE-02 | Phase 1 | Pending |
| TUNE-03 | Phase 1 | Pending |
| TUNE-04 | Phase 1 | Pending |
| TUNE-05 | Phase 1 | Pending |
| MOM-01 | Phase 2 | Pending |
| MOM-02 | Phase 2 | Pending |
| MOM-03 | Phase 2 | Pending |
| MOM-04 | Phase 2 | Pending |
| MR-01 | Phase 2 | Pending |
| MR-02 | Phase 2 | Pending |
| MR-03 | Phase 2 | Pending |
| MR-04 | Phase 2 | Pending |
| LIQ-01 | Phase 3 | Pending |
| LIQ-02 | Phase 3 | Pending |
| CORR-01 | Phase 3 | Pending |
| CORR-02 | Phase 3 | Pending |
| CORR-03 | Phase 3 | Pending |
| RT-01 | Phase 3 | Pending |
| RT-02 | Phase 3 | Pending |
| FUND-01 | Phase 4 | Pending |
| FUND-02 | Phase 4 | Pending |
| FUND-03 | Phase 4 | Pending |
| OBI-01 | Phase 4 | Pending |
| OBI-02 | Phase 4 | Pending |
| OBI-03 | Phase 4 | Pending |
| OBI-04 | Phase 4 | Pending |
| VWAP-01 | Phase 4 | Pending |
| VWAP-02 | Phase 4 | Pending |
| VWAP-03 | Phase 4 | Pending |
| VWAP-04 | Phase 4 | Pending |
| XQ-01 | Phase 5 | Pending |
| XQ-02 | Phase 5 | Pending |
| XQ-03 | Phase 5 | Pending |
| XQ-04 | Phase 5 | Pending |
| XQ-05 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 43 total
- Mapped to phases: 43
- Unmapped: 0

---
*Requirements defined: 2026-03-21*
*Last updated: 2026-03-21 after roadmap creation*
