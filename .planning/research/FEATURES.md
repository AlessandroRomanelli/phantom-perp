# Feature Landscape

**Domain:** Perpetual futures trading strategy bot (Coinbase INTX)
**Researched:** 2026-03-21

## Table Stakes

Features the bot must have or it underperforms. Missing any of these means leaving money on the table compared to even basic quant setups.

### TS-1: Per-Instrument Parameter Tuning

| Attribute | Detail |
|-----------|--------|
| What | Separate strategy parameters for each instrument (ETH, BTC, SOL, QQQ, SPY) rather than one-size-fits-all defaults |
| Why Expected | ETH weekends are dead-volatility while BTC holds flow; SPY/QQQ only trade equity hours. A 2% OI drop threshold that works for ETH is wrong for BTC. Academic research (MDPI 2025) confirms optimal Bollinger width depends on asset-specific volatility regime. |
| Data Needed | Already available -- just different YAML config values per instrument. The `configs/strategies/<strategy>.yaml` already supports `instruments:` key. |
| Complexity | **Low** -- config-only changes, no code changes to strategy logic |
| Notes | This is the single highest-ROI improvement. Every strategy benefits immediately. Should be phase 1. |

### TS-2: Adaptive Conviction Thresholds Based on Volatility

| Attribute | Detail |
|-----------|--------|
| What | Scale min_conviction and signal thresholds dynamically with current vs historical volatility, rather than static thresholds |
| Why Expected | The bot is too quiet in low-vol periods (paper trading showed minimal weekend activity). Static thresholds that work in high-vol miss opportunities in calm markets. Standard practice in 2025 quant systems. |
| Data Needed | `volatility_1h`, `volatility_24h` already in MarketSnapshot. ATR already computed by every strategy. Need a rolling volatility percentile (easily computed from FeatureStore ATR history). |
| Complexity | **Medium** -- requires a volatility regime classifier (simple percentile-based is fine) and wiring it into each strategy's conviction model |
| Notes | Implement as a shared utility that strategies call, not per-strategy duplication. |

### TS-3: Funding Rate Arbitrage Strategy

| Attribute | Detail |
|-----------|--------|
| What | Trade funding rate dislocations. When funding is extreme positive, short the perp (collect funding). When extreme negative, long the perp. Use predicted vs actual rate divergence for entry timing. |
| Why Expected | Funding arb is the most basic perp-specific edge. Average funding rates in 2025 are ~0.015% per 8-hour period (up 50% from 2024). SignalSource.FUNDING_ARB already has a slot reserved. Gate.io reports 19.26% average annual return from funding arb in 2025. Every serious perp bot has this. |
| Data Needed | `funding_rate`, `next_funding_time`, `hours_since_last_funding` all in MarketSnapshot. `funding_rates` array in FeatureStore. **All available.** |
| Complexity | **Medium** -- new strategy file, funding rate z-score computation, time-to-funding decay logic, carry cost awareness |
| Edge | Fires around funding settlement windows (every 8 hours for crypto). Most active when funding is >2 standard deviations from mean. |

### TS-4: Improved Momentum -- Volume Confirmation

| Attribute | Detail |
|-----------|--------|
| What | Add volume confirmation to EMA crossover signals. Crossovers on declining volume are unreliable; crossovers with volume expansion are high-conviction. |
| Why Expected | Current momentum strategy ignores volume entirely. Volume is the most basic confirmation filter -- momentum without volume is a 1990s backtest, not a 2025 strategy. |
| Data Needed | `volumes` array in FeatureStore (24h rolling volume). Need to compute volume rate-of-change, not just absolute level. Already collected but unused. |
| Complexity | **Low** -- add volume ratio check to existing evaluate(), adjust conviction model |
| Notes | Volume data is 24h rolling (`volume_24h`), not per-bar volume. This limits granularity but still provides trend confirmation via rate of change. |

### TS-5: Improved Mean Reversion -- Trend-Aware Filtering

| Attribute | Detail |
|-----------|--------|
| What | Better regime awareness: reject mean reversion signals when a strong trend is establishing (not just ADX > threshold). Use EMA slope direction and momentum strength, not just ADX level. |
| Why Expected | Current implementation uses a single ADX threshold (adx_max=25) which is too blunt. Mean reversion during trend establishment is the #1 way these strategies blow up. Need multi-factor trend rejection. |
| Data Needed | Already available -- closes, highs, lows for EMA slope computation. Could also use the regime_trend strategy's filter outputs (but strategies are independent). |
| Complexity | **Low** -- add EMA slope check and perhaps a lookback for consecutive closes above/below EMA |

### TS-6: Dynamic Stop Placement

| Attribute | Detail |
|-----------|--------|
| What | Stops based on market structure (recent swing high/low, volume nodes) rather than fixed ATR multiples. Trailing stops for trend strategies. |
| Why Expected | Fixed ATR multiplier stops (2x ATR, 1.5x ATR) are a starting point, not an endpoint. They get stopped out on normal retracement in strong trends, and are too wide in low-vol environments. Every production bot uses structure-aware stops. |
| Data Needed | Highs/lows in FeatureStore for swing detection. ATR for scaling. All available. |
| Complexity | **Medium** -- swing point detection algorithm, trailing stop state management (requires tracking active signals post-emission) |
| Notes | Trailing stops require the strategy to maintain state about emitted signals, or the execution layer to handle it. Check if execution layer supports trailing stop updates -- if not, this becomes higher complexity. |

### TS-7: Portfolio A Routing for All Strategies

| Attribute | Detail |
|-----------|--------|
| What | High-conviction signals from any strategy should be eligible for autonomous Portfolio A execution, not just regime_trend and liquidation_cascade |
| Why Expected | Currently only regime_trend and liquidation_cascade route to Portfolio A. A high-conviction mean reversion at 3+ sigma deviation should also be autonomous. Leaving this out means the bot sits idle when high-quality signals appear from other strategies. |
| Data Needed | No new data -- just conviction threshold checks and PortfolioTarget.A routing in each strategy. |
| Complexity | **Low** -- add portfolio_a parameters to each strategy's params dataclass and routing logic in evaluate() |

## Differentiators

Features that provide competitive advantage. Not expected but create meaningful edge when present.

### D-1: Orderbook Imbalance Strategy

| Attribute | Detail |
|-----------|--------|
| What | Dedicated strategy using bid/ask depth imbalance as a directional signal. When bid volume vastly exceeds ask volume near the spread, price tends to rise (and vice versa). Detect absorption events (large orders being filled without price movement = strong support/resistance). |
| Why Expected | SignalSource.ORDERBOOK_IMBALANCE already has a slot. Research (Towards Data Science, QuestDB) confirms OBI has predictive power for short-term price moves in crypto. |
| Data Needed | `orderbook_imbalance` in MarketSnapshot (already collected). However, current imbalance is a single float -- for absorption/sweep detection, we would need order-by-order flow data which is NOT available. The single imbalance value limits this to basic threshold strategies. |
| Complexity | **Medium** -- new strategy file, but limited by data granularity. Basic version (threshold on imbalance) is easy; sophisticated version (absorption, sweep) requires data pipeline changes which are out of scope. |
| Edge | Fires on extreme orderbook skew. Short time horizon (30min-2hr). Good for quick scalp signals. |
| Notes | The single `orderbook_imbalance` float constrains sophistication. Do the basic version first. Do NOT build an order-flow infrastructure -- that's a separate project. |

### D-2: VWAP Deviation Strategy

| Attribute | Detail |
|-----------|--------|
| What | Use deviation from session VWAP as a mean reversion anchor. Price far above VWAP = overextended long, likely to revert. Price far below VWAP = undervalued for the session. Time-of-day awareness for reset windows. |
| Why Expected | VWAP is the institutional benchmark for execution quality. In crypto, VWAP reset at midnight UTC is standard convention. Academic research (arXiv 2025) specifically studied VWAP in crypto perpetuals. |
| Data Needed | Need to compute VWAP from closes and volumes in FeatureStore. Problem: `volume_24h` is a rolling 24h number, NOT per-bar volume. True VWAP requires per-bar price * per-bar volume. **This is a data limitation.** We can approximate using volume deltas between samples, but accuracy is degraded. |
| Complexity | **Medium-High** -- VWAP computation from available data is approximate at best. Need session reset logic. Time-of-day awareness for crypto (24/7) vs equity (market hours) adds branching. |
| Notes | Mark as requiring a feasibility check. The 24h rolling volume vs per-bar volume issue may make true VWAP unreliable. An approximation using volume rate-of-change between samples could work but needs validation. |

### D-3: Volume Profile (High-Volume Node) Strategy

| Attribute | Detail |
|-----------|--------|
| What | Build a price-volume histogram over rolling windows. High-volume nodes (HVN) act as support/resistance. Low-volume gaps are breakout acceleration zones. Trade bounces off HVN and breakouts through low-volume areas. |
| Why Expected | Volume profile is standard in futures trading. NinjaTrader and TradingView both feature it prominently. Institutional futures traders rely on it. |
| Data Needed | Per-bar price and volume data. Same limitation as VWAP -- we have 24h rolling volume, not per-bar volume. Can approximate a distribution using close prices at each sample with equal weighting, but this loses the volume dimension that makes volume profile valuable. |
| Complexity | **High** -- price-volume histogram computation, HVN/LVN detection algorithm, support/resistance level identification, all degraded by volume data limitation |
| Notes | Without per-bar volume, volume profile becomes a price profile. The "volume" part is what provides the edge. **Defer this unless per-bar volume is added to the data pipeline.** |

### D-4: Multi-Window Basis Analysis for Correlation Strategy

| Attribute | Detail |
|-----------|--------|
| What | Enhance the existing correlation strategy with multiple lookback windows for basis z-score (short: 30 bars, medium: 60 bars, long: 120 bars). Signal fires when multiple windows agree. Also integrate funding rate as a third factor -- extreme funding + extreme basis = higher conviction. |
| Why Expected | Current implementation uses a single 60-bar lookback. Multi-timeframe analysis is proven to filter false signals. Adding funding rate creates a three-factor model (basis + OI divergence + funding) that is more robust. |
| Data Needed | All available: closes, index_prices for basis; open_interests for divergence; funding_rates for funding integration. |
| Complexity | **Medium** -- extend existing strategy logic, add funding rate z-score computation, multi-window agreement logic |
| Edge | Fires less often but with much higher accuracy. The three-factor agreement requirement eliminates most false signals. |

### D-5: Graduated Liquidation Cascade Response

| Attribute | Detail |
|-----------|--------|
| What | Replace binary fade/follow with graduated response levels. Tier 1 (mild OI drop 2-4%): reduce position size, widen stops. Tier 2 (moderate 4-8%): standard signal. Tier 3 (severe >8%): aggressive fade with tighter stops, higher conviction. Also add volume surge confirmation -- real liquidations come with volume spikes. |
| Why Expected | Current implementation is binary: either the threshold triggers or it doesn't. Real cascade events have stages. Volume confirmation distinguishes genuine forced liquidations from organic OI reduction. |
| Data Needed | OI data (available), volume data (available for surge detection), ATR (available). |
| Complexity | **Medium** -- restructure evaluate() into tiered logic, add volume spike detection |

### D-6: Cross-Strategy Signal Quality Scoring

| Attribute | Detail |
|-----------|--------|
| What | Normalize conviction scores across strategies so that a 0.7 from momentum means the same thing as a 0.7 from mean_reversion. Currently each strategy has its own conviction model with different scaling, making cross-strategy comparison unreliable. |
| Why Expected | The alpha combiner aggregates signals from all strategies. If conviction scales differ, the combiner makes bad decisions. This is a correctness issue as much as a feature. |
| Data Needed | No new data -- this is a conviction normalization framework. Requires historical signal accuracy data for calibration (not currently tracked). |
| Complexity | **Medium-High** -- need to define a shared conviction framework, retrofit all strategies, ideally calibrate against historical outcomes (which requires logging infrastructure) |
| Notes | Can start with a simple approach: define conviction bands (0.5-0.6 = low, 0.6-0.75 = medium, 0.75+ = high) and ensure each strategy maps to those bands consistently. Full calibration comes later. |

### D-7: Time-of-Day / Session Awareness

| Attribute | Detail |
|-----------|--------|
| What | Strategy parameters that adapt based on trading session. Crypto perps (ETH/BTC/SOL) have known patterns: Asian session low-vol, US session high-vol, weekend doldrums. Equity perps (QQQ/SPY) only have meaningful flow during market hours. |
| Why Expected | The bot's low weekend activity is a direct symptom of not accounting for session dynamics. Lowering thresholds during quiet periods and tightening during active periods improves signal frequency without sacrificing quality. |
| Data Needed | `snapshot.timestamp` (already available). Need a session classifier utility (hour-of-day + day-of-week -> session label). |
| Complexity | **Medium** -- session classifier is simple, but wiring session-dependent parameters into every strategy is repetitive work. Consider a config structure like `instruments: ETH-PERP: sessions: us_hours: { adx_threshold: 18 }` |

## Anti-Features

Features to deliberately NOT build. These are tempting but wrong for this milestone.

### AF-1: Machine Learning Parameter Optimization

| Anti-Feature | ML-based auto-tuning of strategy parameters |
|--------------|------|
| Why Avoid | Explicitly out of scope in PROJECT.md. ML optimization requires backtesting infrastructure that doesn't exist yet. Without proper walk-forward validation, ML will overfit to historical data and blow up in production. The 2025 research (MDPI, Springer) showing ML success all use proper backtesting frameworks. |
| What to Do Instead | Per-instrument manual tuning based on asset characteristics. Start with sensible defaults, observe paper trading, adjust. |

### AF-2: Grid Trading / DCA Strategies

| Anti-Feature | Grid bots or dollar-cost averaging strategies |
|--------------|------|
| Why Avoid | These are fundamentally different strategy types (market-making / systematic entry) that don't fit the signal-based architecture. The bot emits directional signals with conviction scores -- grid trading is bidirectional continuous market-making. Wrong paradigm. |
| What to Do Instead | The existing mean_reversion strategy handles the "buy low sell high" thesis in a signal-compatible way. |

### AF-3: Cross-Exchange Arbitrage

| Anti-Feature | Arbitrage between exchanges (Coinbase INTX vs Binance funding rates, etc.) |
|--------------|------|
| Why Avoid | Requires multi-exchange connectivity, separate capital pools, latency-sensitive execution -- completely different infrastructure. The bot only connects to Coinbase INTX. |
| What to Do Instead | Single-exchange funding rate arbitrage (TS-3) captures the same thesis with existing infrastructure. |

### AF-4: High-Frequency Orderbook Microstructure

| Anti-Feature | Tick-by-tick order flow analysis, spoofing detection, latency arbitrage |
|--------------|------|
| Why Avoid | The FeatureStore samples at 60-second intervals. HFT requires sub-millisecond data. The architecture is fundamentally wrong for this -- and that's by design (the bot is a medium-frequency signal generator, not an HFT system). |
| What to Do Instead | Use the basic orderbook_imbalance signal (D-1) which works at 60-second granularity. |

### AF-5: On-Chain / Sentiment Data Integration

| Anti-Feature | Whale wallet tracking, social sentiment, on-chain flow analysis |
|--------------|------|
| Why Avoid | Explicitly out of scope in PROJECT.md. Requires external data providers and new ingestion pipelines. |
| What to Do Instead | Focus on improving usage of data already collected (funding rates, volume, OI) which are underutilized. |

### AF-6: Full Volume Profile with Per-Bar Volume

| Anti-Feature | Building a per-bar volume ingestion pipeline to support true volume profile |
|--------------|------|
| Why Avoid | Requires changes to the data pipeline (ingestion agent, FeatureStore schema) which is out of scope. The constraint is "limited to data already in MarketSnapshot and FeatureStore." |
| What to Do Instead | Use price distribution analysis (price histogram without volume weighting) as a lighter approximation. Accept that it's weaker than true volume profile. |

## Feature Dependencies

```
Per-Instrument Tuning (TS-1) → independent, no dependencies, do first

Adaptive Conviction (TS-2) → requires volatility percentile utility
  └── All strategy improvements benefit from this

Volume Confirmation (TS-4) → independent addition to momentum
Trend-Aware Filtering (TS-5) → independent addition to mean reversion
Dynamic Stops (TS-6) → independent but complex, may need execution layer check

Funding Rate Arb (TS-3) → independent new strategy
Orderbook Imbalance (D-1) → independent new strategy
VWAP Deviation (D-2) → needs feasibility check on volume data

Multi-Window Basis (D-4) → depends on existing correlation strategy
Graduated Cascade (D-5) → depends on existing liquidation strategy
Portfolio A Routing (TS-7) → should come AFTER strategy improvements stabilize

Cross-Strategy Scoring (D-6) → should come LAST, after all strategies are improved
Time-of-Day Awareness (D-7) → can be done alongside per-instrument tuning
```

## MVP Recommendation

**Phase 1 -- Foundation (do first, highest ROI):**
1. **TS-1: Per-Instrument Tuning** -- pure config, immediate impact, zero risk
2. **TS-4: Volume Confirmation for Momentum** -- easy win, data already available
3. **TS-5: Trend-Aware Filtering for Mean Reversion** -- easy win, reduces false signals

**Phase 2 -- New Strategies:**
4. **TS-3: Funding Rate Arbitrage** -- new strategy, data ready, proven edge
5. **D-1: Orderbook Imbalance (basic)** -- new strategy, data ready, slot reserved

**Phase 3 -- Strategy Enhancement:**
6. **TS-2: Adaptive Conviction Thresholds** -- cross-cutting improvement
7. **D-4: Multi-Window Basis Analysis** -- correlation strategy upgrade
8. **D-5: Graduated Liquidation Cascade** -- liquidation strategy upgrade
9. **D-7: Time-of-Day Awareness** -- improves signal frequency in quiet periods

**Phase 4 -- Polish:**
10. **TS-7: Portfolio A Routing** -- after strategies are improved
11. **TS-6: Dynamic Stops** -- medium complexity, needs execution layer check
12. **D-6: Cross-Strategy Scoring** -- normalization after all strategies stable

**Defer:**
- **D-2: VWAP Deviation** -- feasibility concern with 24h rolling volume data. Investigate in phase 2, implement only if volume delta approximation proves reliable.
- **D-3: Volume Profile** -- defer until per-bar volume is available in data pipeline.

## Sources

- [Gate.io: Perpetual Contract Funding Rate Arbitrage Strategy 2025](https://www.gate.com/learn/articles/perpetual-contract-funding-rate-arbitrage/2166) -- funding rate returns and mechanics
- [CoinGape: 6 Proven Crypto Perpetual Futures Trading Strategies](https://coingape.com/blog/crypto-perpetual-futures-trading-strategies/) -- strategy landscape
- [Towards Data Science: Price Impact of Order Book Imbalance in Cryptocurrency Markets](https://towardsdatascience.com/price-impact-of-order-book-imbalance-in-cryptocurrency-markets-bf39695246f6/) -- OBI predictive power
- [MDPI: Adaptive Optimization of Dual Moving Average Strategy for Automated Cryptocurrency Trading](https://www.mdpi.com/2227-7390/13/16/2629) -- adaptive parameter research
- [arXiv: Deep Learning for VWAP Execution in Crypto Markets](https://arxiv.org/html/2502.13722v1) -- VWAP in crypto perpetuals
- [arXiv: Systematic Trend-Following with Adaptive Portfolio Construction](https://arxiv.org/html/2602.11708v1) -- trailing stops and regime adaptation
- [Medium: Multi-Timeframe Adaptive Market Regime Quantitative Trading Strategy](https://medium.com/@FMZQuant/multi-timeframe-adaptive-market-regime-quantitative-trading-strategy-1b16309ddabb) -- regime-based parameter adaptation
- [QuantStrategy.io: Order Book Imbalances - A Practical Guide](https://quantstrategy.io/blog/order-book-imbalances-a-practical-guide-for-day-traders/) -- OBI implementation patterns
- [Mudrex: VWAP in Crypto 2025](https://mudrex.com/learn/vwap-in-crypto/) -- VWAP crypto conventions
- [NinjaTrader: Volume Analysis in Futures](https://ninjatrader.com/futures/blogs/volume-analysis-in-futures-trading/) -- volume profile patterns
