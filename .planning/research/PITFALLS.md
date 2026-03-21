# Domain Pitfalls

**Domain:** Perpetual futures trading strategy enhancement
**Researched:** 2026-03-21

## Critical Pitfalls

Mistakes that cause rewrites, capital loss, or fundamental strategy failure.

### Pitfall 1: Signal Correlation Cascade — All Strategies Long at the Same Time

**What goes wrong:** Momentum, regime_trend, and correlation strategies share overlapping inputs (EMA slopes, ADX, price direction). During a strong uptrend, all five strategies fire LONG simultaneously. The alpha combiner's agreement boost (+0.05 per additional aligned source, up to +0.20) amplifies conviction further. The system enters a maximum-conviction LONG trade that is actually a single bet amplified through correlated indicators, not a diversified consensus.

**Why it happens:** Every strategy currently reads from the same FeatureStore (same closes, highs, lows). Momentum uses EMA crossover + ADX. Regime_trend uses EMA slope + ADX. Mean reversion fires only when ADX is low, but when it is silent the remaining strategies share positive-trend bias. Correlation strategy's basis z-score can also trend-follow when basis widens during momentum moves. The combiner treats 4 aligned signals as strong agreement, but it is measuring the same underlying price move through 4 correlated lenses.

**Consequences:**
- Position sizing reflects artificially inflated conviction (0.85+ when true edge might be 0.50)
- Max concurrent positions across portfolios A and B can fill simultaneously on the same instrument and direction
- A single reversal wipes multiple positions because they were entered on the same thesis
- Risk limits allow this because each signal looks independent to the risk agent

**Prevention:**
- Add a **signal correlation tracker** in the alpha combiner: compute the rolling correlation between strategy signals over the past N windows, and discount the agreement boost when source correlation exceeds a threshold (e.g., if momentum and regime_trend have >0.7 signal correlation over the last 50 evaluations, treat them as a single source)
- Cap the number of same-direction ideas per instrument per window (e.g., max 1 LONG idea per instrument per 5 minutes regardless of how many strategies agree)
- Add a "unique information" requirement: the combiner should only boost conviction when contributing strategies use genuinely different data (e.g., orderbook flow + price momentum = boost; momentum + regime_trend = no boost because both are price-trend strategies)

**Detection:** Log the contributing_signals metadata on every trade idea. If >80% of ideas have 3+ contributing signals all from price-based strategies, correlation is too high.

**Phase:** Address when improving the alpha combiner / cross-strategy signal quality (early -- before adding more strategies that will make this worse).

---

### Pitfall 2: Overfitting Per-Instrument Parameters

**What goes wrong:** When tuning parameters separately for ETH, BTC, SOL, QQQ, and SPY, it is easy to find parameter sets that perform well on recent data but fail on new data. With 5 instruments x ~10 parameters per strategy x 5 strategies = 250 parameter choices, the degrees of freedom are enormous relative to the signal count.

**Why it happens:** Without a backtesting framework (explicitly out of scope), parameter tuning is done by observing paper trading results over days/weeks. A short observation window (the FeatureStore only holds ~8 hours) combined with recency bias leads to fitting parameters to the last few market conditions. ETH weekend behavior differs from ETH weekday behavior, which differs from ETH during a trending month vs a ranging month.

**Consequences:**
- Parameters tuned to the current regime fail when regime changes (e.g., SOL parameters tuned during a bull run produce false signals during consolidation)
- Tighter thresholds generate more signals initially but produce lower win rates over time
- Time spent tuning 250 parameters without systematic validation is wasted effort

**Prevention:**
- **Constrain the tuning space.** Only tune 2-3 parameters per instrument per strategy: the ones with clear instrument-specific rationale (e.g., ATR multipliers for stop-loss should differ between SOL at 60% annualized vol and SPY at 15% annualized vol, but EMA periods should not differ much). Keep most parameters at sensible defaults.
- **Use volatility-normalized parameters** instead of absolute values. If stop_loss_atr_mult is 2.0, it already adapts to instrument volatility through ATR. Do not also change it per instrument unless there is a structural reason (e.g., SPY-PERP has wider spreads during off-hours).
- **Document the rationale for every per-instrument override.** If the only rationale is "it backtested better," do not ship it.
- **Implement a minimum observation period** before declaring parameter changes successful: at least 2 full market cycles (trending + ranging) per instrument.

**Detection:** Track signal win rate per instrument per strategy over rolling 7-day and 30-day windows. If 7-day win rate is >70% but 30-day is <45%, the parameters are overfit to recent conditions.

**Phase:** Address during the per-instrument parameter tuning phase. Establish the tuning discipline before touching any YAML files.

---

### Pitfall 3: Look-Ahead Bias in Indicator Computation

**What goes wrong:** Indicators computed over the FeatureStore buffer can accidentally incorporate current-bar data in ways that would not be available in real-time. The FeatureStore's `update()` method appends the current price, and then strategies immediately compute indicators over `store.closes` -- which includes the current bar. If an indicator uses `closes[-1]` as both part of the computation window and as the signal trigger, the signal is using information that would not be fully "settled" in a live candle.

**Why it happens:** The FeatureStore samples at 60-second intervals but the sample is taken at a point in time, not at candle close. The "close" price is actually the last-seen price at sampling time, which may be mid-candle. All current strategies use `closes[-1]` for both indicator computation and entry decisions. In the momentum strategy (line 119-123), the EMA is computed over all closes including the current bar, and the crossover is detected on `fast[-1]` vs `slow[-1]`. If the sample was taken mid-move, the crossover may not persist by the next sample.

**Consequences:**
- Signals trigger on intra-candle moves that reverse by bar close
- Paper trading results look better than they should because the "entry" is the price that triggered the signal, which is the extreme of the move
- Strategies appear to have better timing than they actually do

**Prevention:**
- For crossover detection, use `closes[-2]` vs `closes[-3]` (previous completed bar) as the "current" comparison, and `closes[-1]` only to confirm the move is continuing
- Add a **confirmation delay**: require the crossover/breach to persist for at least 1 additional bar before signaling (the cooldown_bars mechanism partially does this, but it prevents re-signaling rather than confirming the initial signal)
- When computing z-scores, rolling means, and standard deviations, exclude the current bar from the window: use `series[:-1]` for the statistics, then compare the current value against them

**Detection:** Compare signal entry prices against the actual close of the bar that triggered the signal. If entry prices consistently outperform bar closes (buying lower, selling higher than the bar close), look-ahead bias is present.

**Phase:** Address when improving each strategy. This is a per-strategy code change, not a systemic one.

---

### Pitfall 4: Funding Rate Strategy — Trading Already-Priced-In Information

**What goes wrong:** The funding rate on Coinbase INTX settles hourly. The current rate is publicly visible and already reflected in the basis between mark price and index price. A naive funding rate arbitrage strategy that goes long when funding is negative (expecting to receive funding) or short when funding is positive (expecting to pay less than the market) trades on information the market has already priced. The basis z-score in the correlation strategy already captures this relationship.

**Why it happens:** Funding rate looks like free alpha: "just collect the funding payment." But the cost of entering the position (adverse selection, spread, and basis movement) typically exceeds the funding payment. Funding rates of 0.01% per hour (the typical range) yield ~$2.50 per $25,000 position per hour. The taker fee at VIP1 (0.025%) on a $25,000 entry+exit is $12.50 -- five hours of funding just to cover fees.

**Consequences:**
- Strategy enters positions that are net-negative after fees and spread
- In trending markets, funding rate is persistently positive (longs pay shorts). A short-funding strategy is a disguised counter-trend trade.
- Paper simulator does not simulate funding settlement (known bug in CONCERNS.md), so the funding strategy will appear profitable in paper but lose in live because it is not accounting for the funding payments it supposedly collects

**Prevention:**
- **Do not build a pure funding-rate-collection strategy.** Instead, use funding rate as a **confirmation filter** for other strategies: boost conviction for momentum-SHORT when funding is extremely positive (overcrowded long), boost conviction for mean-reversion-LONG when funding is extremely negative (overcrowded short)
- If building a dedicated funding strategy, require the **predicted vs actual rate divergence** to be significant (>2x the fee cost per settlement), not just "funding is positive/negative"
- Require a minimum holding period of 3+ funding settlements to amortize entry/exit costs
- Fix the paper simulator funding settlement bug BEFORE deploying any funding-aware strategy, otherwise paper results will be misleading

**Detection:** Track funding P&L attribution separately from position P&L. If the funding-aware strategy's position P&L is consistently negative but "funding collected" shows positive, the strategy is losing money entering/exiting and the funding does not cover it.

**Phase:** Address when building the funding rate arbitrage strategy. Fix paper simulator first.

---

### Pitfall 5: Weekend and Low-Liquidity Regime Blindness

**What goes wrong:** All five current strategies use the same thresholds regardless of time-of-day or day-of-week. Paper trading already showed "very low activity on weekends" because thresholds are too conservative. The fix is NOT to simply lower thresholds on weekends -- it is to recognize that weekend markets have structurally different properties: wider spreads, thinner books, more erratic price action, and higher false-signal rates.

**Why it happens:** The FeatureStore does not track time-of-day or day-of-week. Strategies cannot distinguish between "ADX is 18 on a Tuesday" (low activity, no trade) and "ADX is 18 on a Sunday" (normal weekend behavior, tradeable). Similarly, SPY-PERP and QQQ-PERP have equity-market hours where liquidity is concentrated vs overnight gaps where the book is thin.

**Consequences:**
- Lowering thresholds uniformly to increase weekend activity also increases false signals on weekdays
- Weekend mean-reversion signals fire into a thin book, and the entry slippage exceeds the expected reversion
- ATR computed from weekend bars is misleadingly low (price just drifts), leading to stops that are too tight when Monday activity resumes
- SPY-PERP weekend positions accumulate funding costs while doing nothing, because SPY has no spot reference on weekends

**Prevention:**
- **Implement regime-aware thresholds** based on a simple time-of-week classifier: "crypto_weekday", "crypto_weekend", "equity_market_hours", "equity_off_hours". Each regime adjusts conviction thresholds, minimum ATR requirements, and position sizing
- **Widen stops on weekends** or during off-hours to account for gap risk when liquidity returns
- For SPY-PERP and QQQ-PERP, reduce or disable most strategies outside equity market hours (9:30-16:00 ET). The orderbook and volume data is unreliable outside these hours
- Do NOT lower conviction thresholds as the primary fix for weekend inactivity. Instead, add strategies that are designed for low-vol environments (e.g., funding rate filter, tight range-bound scalping)

**Detection:** Track signal frequency and win rate by hour-of-week (168 buckets). Identify time windows where signal frequency drops >80% vs the mean or win rate drops below 40%.

**Phase:** Address as part of per-instrument parameter tuning, but the time-classifier infrastructure should be built first as a shared utility.

## Moderate Pitfalls

### Pitfall 6: Conviction Inflation Through the Alpha Combiner

**What goes wrong:** The alpha combiner applies an agreement boost (+0.05 per additional aligned source, max +0.20) and regime boosts (up to 1.3x). These multiplicative and additive factors can inflate a moderate 0.55 conviction into a 0.85+ conviction that triggers Portfolio A autonomous execution and larger position sizing, without any new information.

**Prevention:**
- Cap the final conviction at the highest individual signal conviction + 0.10 (agreement should add modest confidence, not transform weak signals into strong ones)
- Audit the regime boost table: currently MOMENTUM gets 1.3x in TRENDING_UP, which rewards the strategy most likely to already be firing. This creates positive feedback (trend -> momentum fires -> regime detector says trending -> momentum gets boosted -> higher conviction -> bigger position)
- Add a **conviction budget** per time window: the total conviction emitted across all ideas in a 1-hour window should not exceed a cap (e.g., 3.0 total conviction), forcing the system to be selective

**Detection:** Plot the distribution of final idea conviction vs individual signal conviction. If the mean conviction uplift from combiner is >0.15, the combiner is inflating.

**Phase:** Address when improving cross-strategy signal quality.

---

### Pitfall 7: FeatureStore Data Horizon Too Short for New Strategies

**What goes wrong:** The FeatureStore holds 500 samples at 60-second intervals = ~8.3 hours. The regime_trend strategy already requires min_history of ~65 bars. A VWAP strategy needs a full session (24h for crypto, 6.5h for equities). A volume profile strategy needs multiple sessions to establish high-volume nodes. Neither can work with 8 hours of 1-minute data.

**Prevention:**
- Extend FeatureStore to support multiple timeframes: keep the 60s buffer for short-term strategies, add a 5m or 15m buffer (500 samples at 15m = 5+ days) for medium-term strategies
- For VWAP: use a dedicated session accumulator that resets at a configurable session boundary (00:00 UTC for crypto, 09:30 ET for equities), not the rolling buffer
- For volume profile: precompute and persist profile levels in TimescaleDB (already available per CONCERNS.md), load on startup
- Do NOT increase max_samples to 5000+ on the 60s buffer -- memory usage will balloon and indicator computation will slow down on every tick

**Detection:** New strategies that need >500 bars should fail gracefully with a clear log message rather than silently returning empty signals.

**Phase:** Address before building VWAP and volume profile strategies. The FeatureStore extension should be in the infrastructure phase.

---

### Pitfall 8: Orderbook Imbalance False Signals in Thin Books

**What goes wrong:** The planned orderbook imbalance strategy uses bid/ask depth to detect absorption, sweeps, and directional flow. On Coinbase INTX perps (especially SOL-PERP, QQQ-PERP, SPY-PERP), the orderbook is often thin. A single market maker refreshing quotes can swing the imbalance metric from -0.5 to +0.5 in seconds. This is noise, not signal.

**Prevention:**
- **Require minimum book depth** before trusting imbalance signals: if total visible depth (bid + ask within 50bps of mid) is below a threshold, suppress the strategy
- Use **time-weighted imbalance** (average imbalance over 5+ minutes) rather than point-in-time imbalance
- Distinguish between **resting order flow** (persistent imbalance that survives multiple ticks) and **flickering flow** (imbalance that appears and disappears within seconds)
- The current liquidation_cascade strategy already uses `snapshot.orderbook_imbalance` as a point-in-time metric. This is acceptable for confirming a cascade (which has other triggers) but insufficient as a primary signal

**Detection:** Track the autocorrelation of orderbook_imbalance at lag-1 (60s). If autocorrelation is <0.3, the metric is mostly noise and should not be used as a primary trigger.

**Phase:** Address when building the orderbook imbalance strategy.

---

### Pitfall 9: Adverse Selection on Portfolio A Autonomous Trades

**What goes wrong:** Portfolio A executes autonomously without user confirmation. The routing rules send short time-horizon (<2h) and high-conviction signals to Portfolio A. But the highest-conviction signals tend to fire at moments of rapid price movement (breakouts, cascades) -- exactly when adverse selection is worst. By the time the order reaches Coinbase, the price has moved.

**Prevention:**
- Use **limit orders with aggressive offset** (already configured: limit_offset_bps=5) rather than market orders for Portfolio A
- Implement a **price staleness check** at execution time: if the entry_price on the signal is >max_slippage_bps (20bps) from current market, reject the idea rather than chasing
- Add a **fill quality tracker**: for each Portfolio A trade, compare the signal's entry_price to the actual fill price. If median slippage exceeds 10bps consistently, tighten the routing criteria
- Consider adding a small execution delay (2-5 seconds) for Portfolio A to let the orderbook stabilize after the event that triggered the signal

**Detection:** Track `(fill_price - signal_entry_price) / signal_entry_price` for all Portfolio A trades. If median is consistently >10bps unfavorable, adverse selection is eating the edge.

**Phase:** Address during dual portfolio routing improvements.

---

### Pitfall 10: Shared Cooldown State Across Instruments

**What goes wrong:** Each strategy instance maintains `_bars_since_signal` as instance state. If a single strategy instance is shared across instruments (called once per instrument per tick), the cooldown from an ETH signal suppresses a BTC signal. Looking at the code, strategies are called with `snapshot.instrument` varying, but `_bars_since_signal` is a single counter on the instance.

**Prevention:**
- Make cooldown state per-instrument: use a `dict[str, int]` keyed by instrument ID instead of a single `int`
- Verify in the signals agent main loop whether each strategy is instantiated once per instrument (correct) or once globally (bug). If global, add per-instrument cooldown tracking.

**Detection:** Check if signals for instrument B are suppressed after a signal fires for instrument A. Log cooldown rejections with the instrument ID.

**Phase:** Address immediately as part of strategy improvement -- this is a potential bug in the current implementation.

## Minor Pitfalls

### Pitfall 11: Volume Data Staleness (24h Rolling)

**What goes wrong:** The FeatureStore stores `volume_24h` from the snapshot, which is a 24-hour rolling volume from Coinbase. This metric changes slowly and does not reflect intra-hour volume surges. A volume profile or VWAP strategy needs cumulative session volume and per-bar volume, not a rolling 24h aggregate.

**Prevention:** Add a `bar_volume` field to FeatureStore that computes the volume delta between consecutive samples. Use this for volume-based strategies, not the raw 24h number.

**Phase:** Address when extending FeatureStore for new strategies.

---

### Pitfall 12: Paper Simulator Funding Bug Invalidates Strategy Comparisons

**What goes wrong:** The paper simulator does not simulate hourly funding settlements (documented in CONCERNS.md). Any strategy comparison that involves holding positions for >2 hours will be systematically biased: long-holding strategies look better than they are (no funding cost), and a funding-rate strategy cannot even be paper-tested.

**Prevention:** Fix the funding settlement simulation before deploying per-instrument parameter tuning or the funding rate strategy. Without this fix, paper trading data is unreliable for any strategy that holds >2 hours.

**Phase:** Fix before any strategy evaluation work begins. This is a blocker for accurate performance measurement.

---

### Pitfall 13: Config Explosion Without Validation

**What goes wrong:** With per-instrument overrides across 5 instruments x 9+ strategies, the YAML config surface becomes large. A typo in a parameter name (e.g., `adx_threhold` instead of `adx_threshold`) silently falls through to the default value because all configs use `.get()` with defaults. The strategy runs with unintended parameters and nobody notices.

**Prevention:**
- Add **config schema validation** using dataclass field names: after loading YAML, verify that all keys in the `parameters:` block match actual fields on the params dataclass. Log a warning for unknown keys.
- Add a **config diff logger** at startup: for each instrument, log which parameters differ from the strategy default and by how much.

**Phase:** Address during per-instrument parameter tuning setup.

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Improve existing strategies | Look-ahead bias (Pitfall 3), cooldown bug (Pitfall 10) | Audit each strategy for bar-alignment; add per-instrument cooldown tracking |
| Per-instrument tuning | Overfitting (Pitfall 2), config explosion (Pitfall 13) | Constrain tuning space to 2-3 params per instrument; validate config schema |
| Funding rate strategy | Already-priced-in (Pitfall 4), paper sim bug (Pitfall 12) | Use as filter not primary signal; fix paper sim first |
| Orderbook imbalance strategy | Thin book noise (Pitfall 8), adverse selection (Pitfall 9) | Minimum depth gate; time-weighted imbalance |
| VWAP / volume profile strategy | FeatureStore too short (Pitfall 7), volume staleness (Pitfall 11) | Extend FeatureStore with multi-timeframe; compute bar_volume deltas |
| Cross-strategy signal quality | Correlation cascade (Pitfall 1), conviction inflation (Pitfall 6) | Signal correlation tracking; conviction budget; cap combiner uplift |
| Dual portfolio routing | Adverse selection (Pitfall 9), cascading positions | Fill quality tracking; price staleness check at execution |
| Weekend activity improvement | Low-liquidity traps (Pitfall 5) | Time-of-week classifier; wider stops; do not just lower thresholds |

## Sources

- Direct code analysis of all 5 strategies, FeatureStore, AlphaCombiner, and ConflictResolver
- PROJECT.md known issues (paper trading low weekend activity, FeatureStore 8h limit, funding/volume underutilization)
- CONCERNS.md (paper simulator funding bug, ingestion data staleness, rate limiter issues)
- configs/default.yaml (risk limits, routing rules, fee structure, execution parameters)
- Confidence: HIGH -- all findings derived from direct codebase analysis and domain knowledge of perpetual futures market microstructure
