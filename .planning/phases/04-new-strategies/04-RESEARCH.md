# Phase 4: New Strategies - Research

**Researched:** 2026-03-22
**Domain:** Trading strategy implementation (funding rate utility, orderbook imbalance strategy, VWAP feasibility)
**Confidence:** HIGH

## Summary

Phase 4 adds three new signal sources to fill coverage gaps. The funding rate filter is a shared utility (not a strategy) that boosts conviction for other strategies' signals when extreme funding aligns with direction. The orderbook imbalance (OBI) strategy is a new `SignalStrategy` subclass that emits short-horizon directional signals from time-weighted bid/ask depth imbalance. The VWAP deviation strategy is feasibility-gated: a programmatic validation must confirm the volume-delta approximation produces usable VWAP values before investing in session resets and deviation signals.

All three components consume data already available in FeatureStore (funding_rates, orderbook_imbalances, bar_volumes, closes, volumes, timestamps). No new data ingestion is needed. The SignalSource enum already has FUNDING_ARB, ORDERBOOK_IMBALANCE, and VWAP entries. The existing strategy registration pattern (STRATEGY_CLASSES dict, strategy_matrix.yaml, per-strategy YAML config) applies to OBI and VWAP.

**Primary recommendation:** Structure work as funding utility first (generalizes Phase 3 pattern, immediately usable by existing strategies), then OBI strategy (straightforward data, proven pattern), then VWAP feasibility gate (may result in deferral, so do last).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Funding rate filter is a shared utility, not a standalone SignalStrategy -- it boosts conviction for other strategies' signals when funding aligns with direction
- **D-02:** Generalize the pattern from Phase 3's correlation funding integration -- make it callable by any strategy
- **D-03:** Boost only, never suppress -- extreme funding aligned with signal direction increases conviction, but opposing funding does NOT reject the signal
- **D-04:** Opt-in per strategy -- strategies call the utility explicitly, not automatically applied
- **D-05:** Claude validates feasibility programmatically -- write a test/validation that checks whether the volume-delta approximation produces usable VWAP values, and auto-decide
- **D-06:** If VWAP approximation fails feasibility, defer the entire VWAP strategy to v2 with documented rationale -- 2 of 3 new strategies (funding filter + orderbook imbalance) is an acceptable Phase 4 outcome
- **D-07:** Alternative VWAP approaches (e.g., rolling price-volume weighted average without session resets) are at Claude's discretion if the standard approach fails
- **D-08:** Shortest possible time horizon -- push to the minimum practical given 60-second FeatureStore sampling (e.g., 1 hour or less)
- **D-09:** Fire often with varying conviction -- catch more imbalance events rather than being highly selective, let conviction differentiate signal quality
- **D-10:** More conservative min_conviction than other strategies -- orderbook data is noisier, so the bar to emit a signal should be higher even though it fires frequently
- **D-11:** Enable for all instruments including equity perps -- let the minimum depth gate (OBI-03) naturally suppress signals on thin orderbooks rather than disabling per instrument

### Claude's Discretion
- Funding rate z-score computation details and thresholds (FUND-02)
- Time-to-funding decay formula (FUND-03)
- How the funding utility integrates with existing strategies (which strategies opt in first)
- VWAP feasibility test design and pass/fail criteria (D-05)
- Alternative VWAP approach if standard fails (D-07)
- Exact OBI time horizon (D-08 -- shortest practical)
- OBI time-weighted imbalance lookback window (OBI-02)
- OBI minimum depth threshold (OBI-03)
- OBI Portfolio A conviction threshold (OBI-04)
- OBI conviction model design

### Deferred Ideas (OUT OF SCOPE)
- VWAP strategy may be fully deferred to v2 if feasibility validation fails (D-06)
- Volume profile strategy (VPRO-01 through VPRO-03) -- already in v2 requirements
- Funding rate as standalone signal emitter -- not needed since it's a utility (D-01)
- Per-instrument momentum tuning -- still pending from Phase 2 (D-08)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FUND-01 | Funding rate as confirmation filter -- boost conviction for momentum-SHORT when funding extreme positive, boost mean-reversion-LONG when funding extreme negative | Funding utility design, correlation pattern generalization, boost-only semantics |
| FUND-02 | Funding rate z-score computation -- rolling z-score of funding rate vs historical distribution | Z-score computation pattern from correlation strategy, sparse funding rate handling |
| FUND-03 | Time-to-funding decay -- signal urgency increases as next funding settlement approaches | MarketSnapshot.hours_since_last_funding field, decay formula design |
| OBI-01 | New strategy using bid/ask depth imbalance as directional signal for short-term trades | SignalStrategy base class, FeatureStore.orderbook_imbalances, strategy registration |
| OBI-02 | Time-weighted imbalance -- average imbalance over multiple samples rather than point-in-time | FeatureStore sampling at 60s intervals, rolling window over imbalance history |
| OBI-03 | Minimum depth gate -- suppress signals when orderbook is too thin to be meaningful | FeatureStore stores raw imbalance values, need absolute depth proxy from volumes/spread |
| OBI-04 | Portfolio A routing -- short time horizon signals route to autonomous execution | Established Portfolio A routing pattern with conviction threshold |
| VWAP-01 | Feasibility validation -- confirm volume-delta approximation produces usable VWAP values | FeatureStore.bar_volumes (np.diff of 24h volumes, can be negative), programmatic validation |
| VWAP-02 | Session VWAP computation -- VWAP with configurable session reset | FeatureStore.timestamps (Unix epoch), closes, bar_volumes for price-volume weighting |
| VWAP-03 | Deviation-based signals -- extreme deviations from session VWAP as mean reversion triggers | Z-score pattern from correlation, deviation threshold design |
| VWAP-04 | Time-of-session awareness -- VWAP signals more reliable later in session | Timestamp-based session progress computation |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| numpy | 1.26+ | Z-score computation, rolling windows, array operations | Already used by all strategies |
| scipy.stats | (bundled) | `percentileofscore` for adaptive thresholds | Already used by momentum, mean reversion, regime trend |
| dataclasses | stdlib | Params classes for OBI and VWAP strategies | Established pattern for all strategy params |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| libs.indicators.volatility.atr | internal | ATR for stop/TP placement | OBI and VWAP stop computation |
| libs.common.utils | internal | `generate_id`, `round_to_tick`, `utc_now` | Signal construction |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual z-score | scipy.stats.zscore | Manual gives more control over edge cases (empty arrays, small samples) -- use manual |
| Point-in-time OBI | Exponential weighted moving average | EWMA gives recency bias but more complex -- use simple average for transparency |

## Architecture Patterns

### Recommended Project Structure
```
agents/signals/
  strategies/
    orderbook_imbalance.py    # New OBI strategy (SignalStrategy subclass)
    vwap.py                   # New VWAP strategy (if feasible)
  funding_filter.py           # Shared utility (NOT a strategy)
  feature_store.py            # Unchanged

configs/strategies/
  orderbook_imbalance.yaml    # Already exists (update parameters)
  vwap.yaml                   # New (if feasible)

tests (in agents/signals/tests/):
  test_funding_filter.py      # New
  test_orderbook_imbalance.py # New
  test_vwap.py                # New (feasibility test + strategy tests if feasible)
```

### Pattern 1: Funding Rate Filter Utility
**What:** A module-level utility class/functions in `agents/signals/funding_filter.py` that any strategy can import and call to get a conviction boost based on funding rate alignment.
**When to use:** Any strategy that wants funding rate confirmation.
**Design:**

```python
# agents/signals/funding_filter.py
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from libs.common.models.enums import PositionSide


@dataclass
class FundingBoostResult:
    """Result of funding rate confirmation check."""
    boost: float           # 0.0 if no boost, positive value if aligned
    z_score: float         # Current funding rate z-score
    decay_factor: float    # Time-to-settlement decay [0, 1]
    aligned: bool          # Whether funding direction aligns with signal


def compute_funding_boost(
    funding_rates: NDArray[np.float64],
    signal_direction: PositionSide,
    hours_since_last_funding: float,
    z_score_threshold: float = 1.5,
    max_boost: float = 0.10,
    lookback: int = 50,
) -> FundingBoostResult:
    """Compute conviction boost from funding rate alignment.

    Boost-only: returns 0.0 boost if funding opposes signal (D-03).
    Strategies opt in by calling this explicitly (D-04).
    """
    ...
```

**Key design decisions:**
- Returns a result dataclass (not just a float) so strategies can include funding metadata
- Takes `hours_since_last_funding` from MarketSnapshot for time-to-settlement decay
- Z-score threshold and max_boost are parameters so strategies can tune them
- Generalizes the pattern from correlation.py lines 170-215

### Pattern 2: Orderbook Imbalance Strategy
**What:** A full `SignalStrategy` subclass that uses time-weighted orderbook imbalance to generate short-horizon directional signals.
**When to use:** When imbalance exceeds thresholds and book depth is sufficient.
**Design:**

```python
# Key parameters
@dataclass
class OrderbookImbalanceParams:
    lookback_bars: int = 10           # 10 bars * 60s = 10 min rolling window (OBI-02)
    imbalance_threshold: float = 0.3  # Minimum time-weighted imbalance to trigger
    min_depth_proxy: float = 0.5      # Min avg absolute imbalance magnitude (OBI-03)
    atr_period: int = 14
    stop_loss_atr_mult: float = 1.5   # Tighter stops for short horizon
    take_profit_atr_mult: float = 2.0
    min_conviction: float = 0.50      # Higher than other strategies (D-10)
    cooldown_bars: int = 3            # Short cooldown for frequent firing (D-09)
    portfolio_a_min_conviction: float = 0.65  # Portfolio A threshold (OBI-04)
```

**Time horizon:** 1 hour (timedelta(hours=1)) -- shortest practical given 60s sampling (D-08). With 10-bar lookback, the signal reflects 10 minutes of imbalance history, and a 1-hour horizon gives the directional pressure time to materialize.

### Pattern 3: VWAP Feasibility Gate
**What:** A programmatic test that validates whether FeatureStore's `bar_volumes` (np.diff of 24h rolling volume) produces usable VWAP values.
**When to use:** Must pass before any VWAP strategy implementation.
**Design:**

```python
# Feasibility criteria:
# 1. bar_volumes must be non-negative for >80% of samples (rolling off creates negatives)
# 2. Cumulative bar_volume must be positive and growing within a session
# 3. Price-volume weighted average must differ from simple average by meaningful amount
# 4. VWAP should be stable (low variance) compared to price itself

# If standard approach fails:
# - Alternative: rolling TWAP (time-weighted average price) without volume weighting
# - Alternative: rolling price-volume weighted average without session resets (D-07)
```

**Critical insight:** `bar_volumes` are `np.diff` of 24h rolling volume. When high-volume bars from 24 hours ago roll off, `bar_volumes` goes negative. This is the key feasibility concern -- negative volumes corrupt VWAP computation. The feasibility test must quantify how often this occurs and whether it can be mitigated (e.g., by clamping negatives to zero).

### Anti-Patterns to Avoid
- **Applying funding filter automatically to all strategies:** D-04 requires opt-in. Each strategy chooses to call the utility.
- **Using funding filter to suppress signals:** D-03 requires boost-only. Opposing funding returns 0.0 boost, not -X.
- **Using point-in-time orderbook imbalance:** OBI-02 requires time-weighted average, not the latest snapshot value.
- **Implementing VWAP before feasibility gate passes:** D-05/D-06 require validation first. Do not invest in session resets until bar_volumes are validated.
- **Making OBI too selective:** D-09 says fire often with varying conviction. The conviction model should have a wide range, not a binary fire/no-fire gate.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Z-score computation | Custom z-score from scratch | Reuse correlation's `_compute_zscore` pattern | Already handles edge cases (small samples, zero std) |
| ATR-based stops | New stop computation | Reuse `round_to_tick(entry +/- atr_d * mult)` pattern from momentum/correlation | Consistent with existing strategies |
| Portfolio A routing | New routing logic | `PortfolioTarget.A if conviction >= threshold else PortfolioTarget.B` | Exact pattern from all strategies |
| Config loading | New config parsing | Existing `CorrelationParams` pattern with `config.get("parameters", {})` | Config validation already handles this |

**Key insight:** Every strategy in the codebase follows the exact same structural pattern. The OBI strategy should be a mechanical copy of the correlation or momentum strategy structure with different signal logic.

## Common Pitfalls

### Pitfall 1: Sparse Funding Rate Data
**What goes wrong:** Funding rates are only appended to FeatureStore when the value changes. The `funding_rates` array can be very short (e.g., 3-5 entries) even after hours of operation.
**Why it happens:** FeatureStore deduplicates funding rates (line 79: `float(snapshot.funding_rate) != self._funding_rates[-1]`).
**How to avoid:** The funding utility must handle arrays with < 10 entries gracefully -- return no-boost rather than computing meaningless z-scores. Use a minimum sample count guard (e.g., `if len(funding_rates) < min_samples: return FundingBoostResult(boost=0.0, ...)`).
**Warning signs:** Z-scores of 0.0 or extreme values (>5) from tiny sample sizes.

### Pitfall 2: Negative Bar Volumes Corrupting VWAP
**What goes wrong:** `bar_volumes` is `np.diff(volumes_24h)`. When high-volume periods from 24 hours ago roll off the window, the delta goes negative. Using these directly in VWAP (sum of price*volume / sum of volume) produces nonsensical results.
**Why it happens:** The 24h rolling window means today's low-volume period minus yesterday's high-volume period = negative delta.
**How to avoid:** The feasibility test must measure the frequency and magnitude of negative bar_volumes. Mitigation options: (a) clamp to zero, (b) use absolute values, (c) fall back to time-weighting. If none work well, defer VWAP per D-06.
**Warning signs:** VWAP values that exceed the price range, negative cumulative volumes.

### Pitfall 3: Orderbook Imbalance Noise on Thin Books
**What goes wrong:** On thin orderbooks (SOL-PERP, equity perps), small order additions/removals cause large imbalance swings. The strategy fires on noise rather than genuine directional pressure.
**Why it happens:** Imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol). When total depth is small, any change is a large percentage.
**How to avoid:** OBI-03 minimum depth gate. Since FeatureStore doesn't store absolute depth (only the imbalance ratio), use a proxy: spread_bps (wide spread = thin book) or the variance of imbalance values (high variance = noisy book). The existing `orderbook_imbalance` field in MarketSnapshot ranges [-1, 1].
**Warning signs:** Very frequent signals on equity perps despite minimal actual depth.

### Pitfall 4: Funding Rate Direction Semantics
**What goes wrong:** Confusing which direction funding "helps." Positive funding = longs pay shorts = bearish pressure (incentivizes shorting). Negative funding = shorts pay longs = bullish pressure.
**Why it happens:** The semantics are counterintuitive (positive rate is bearish, not bullish).
**How to avoid:** Follow the exact pattern from correlation.py lines 176-181: `LONG and cur_funding < 0` confirms, `SHORT and cur_funding > 0` confirms.
**Warning signs:** Boosting LONG signals when funding is positive (wrong direction).

### Pitfall 5: FeatureStore Orderbook Imbalance vs Depth
**What goes wrong:** The `orderbook_imbalances` array stores the ratio [-1, 1] but not the absolute depth. OBI-03 requires a minimum depth gate, but there's no direct depth data in FeatureStore.
**Why it happens:** The normalizer computes imbalance from bid_depth/ask_depth but only stores the ratio.
**How to avoid:** Use spread_bps as a depth proxy (wider spread = thinner book). MarketSnapshot has `spread_bps` and `volatility_1h`. Alternatively, use the magnitude of imbalance changes (volatile imbalance = thin book). The OBI strategy receives the full MarketSnapshot, so it can check `snapshot.spread_bps` directly as a depth gate.
**Warning signs:** Good imbalance signals on instruments with 50+ bps spreads (too thin to be meaningful).

## Code Examples

### Funding Rate Z-Score with Sparse Data Guard
```python
# Source: Generalized from agents/signals/strategies/correlation.py lines 296-310
def _funding_zscore(
    funding_rates: NDArray[np.float64],
    lookback: int = 50,
    min_samples: int = 10,
) -> float:
    """Compute z-score of current funding rate vs rolling history.

    Returns 0.0 if insufficient samples (sparse data guard).
    """
    if len(funding_rates) < min_samples:
        return 0.0
    window = funding_rates[-lookback:] if len(funding_rates) >= lookback else funding_rates
    mean = float(np.mean(window))
    std = float(np.std(window, ddof=1))
    if std < 1e-12:
        return 0.0
    return (float(funding_rates[-1]) - mean) / std
```

### Time-to-Settlement Decay
```python
# Source: MarketSnapshot.hours_since_last_funding is [0, 1] fractional hours
def _settlement_decay(hours_since_last_funding: float) -> float:
    """Compute urgency decay factor based on proximity to next settlement.

    Returns higher values closer to settlement (1.0 at settlement,
    ~0.37 at 30 min before, lower further out).

    Coinbase INTX settles funding every hour.
    hours_since_last_funding is in [0, 1]:
      - 0.0 = just settled
      - ~1.0 = about to settle
    """
    # Exponential decay: urgency increases as we approach settlement
    # At hours_since=0.0 (just settled): factor = exp(-2) = 0.135
    # At hours_since=0.5 (30 min in): factor = exp(-1) = 0.368
    # At hours_since=1.0 (about to settle): factor = exp(0) = 1.0
    return float(np.exp(-2.0 * (1.0 - hours_since_last_funding)))
```

### Time-Weighted Imbalance Computation
```python
# Source: FeatureStore.orderbook_imbalances sampled every 60s
def _time_weighted_imbalance(
    imbalances: NDArray[np.float64],
    lookback: int = 10,
) -> float:
    """Compute time-weighted average of recent imbalance samples.

    More recent samples get higher weight via linear decay.
    """
    if len(imbalances) < lookback:
        window = imbalances
    else:
        window = imbalances[-lookback:]

    if len(window) == 0:
        return 0.0

    # Linear weights: most recent = highest weight
    weights = np.arange(1, len(window) + 1, dtype=np.float64)
    return float(np.average(window, weights=weights))
```

### OBI Conviction Model (3-component)
```python
def _compute_obi_conviction(
    tw_imbalance: float,
    imbalance_threshold: float,
    spread_bps: float,
    volume_ratio: float = 1.0,
) -> float:
    """3-component conviction model for OBI.

    - Imbalance magnitude (0-0.45): scales with how far beyond threshold
    - Spread component (0-0.30): tighter spread = more trustworthy signal
    - Volume component (0-0.25): higher volume confirms depth
    """
    # Imbalance component: scales from 0 at threshold to 0.45 at 2x threshold
    imb_excess = abs(tw_imbalance) - imbalance_threshold
    imb_score = min(max(imb_excess / imbalance_threshold * 0.45, 0.0), 0.45)

    # Spread component: tight spread = good depth (inverse relationship)
    # spread_bps < 5 = excellent, > 20 = poor
    spread_score = max(0.0, min((20.0 - spread_bps) / 20.0 * 0.30, 0.30))

    # Volume component: placeholder for now
    vol_score = min(max((volume_ratio - 0.5) / 2.0, 0.0), 0.25)

    return round(min(imb_score + spread_score + vol_score, 1.0), 3)
```

### Strategy Registration Pattern
```python
# In agents/signals/main.py -- add to STRATEGY_CLASSES and STRATEGY_PARAMS_CLASSES:
from agents.signals.strategies.orderbook_imbalance import (
    OrderbookImbalanceParams, OrderbookImbalanceStrategy,
)

STRATEGY_CLASSES["orderbook_imbalance"] = OrderbookImbalanceStrategy
STRATEGY_PARAMS_CLASSES["orderbook_imbalance"] = OrderbookImbalanceParams

# In configs/strategy_matrix.yaml -- add:
# orderbook_imbalance:
#   enabled: true
#   instruments:
#     - ETH-PERP
#     - BTC-PERP
#     - SOL-PERP
#     - QQQ-PERP
#     - SPY-PERP
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Funding inline in correlation | Shared utility callable by any strategy | Phase 4 | Generalizes funding confirmation |
| No short-horizon signals | OBI with 1-hour horizon | Phase 4 | Fills coverage gap for rapid directional moves |
| No VWAP | Feasibility-gated VWAP or deferral | Phase 4 | Volume-delta approximation may not work |

**Data constraints:**
- FeatureStore samples at 60s intervals (min practical horizon ~1 hour)
- Funding rates are sparse (only on change)
- bar_volumes can be negative (24h rolling window artifact)
- orderbook_imbalance is a ratio [-1, 1], no absolute depth stored

## Open Questions

1. **Depth proxy for OBI-03**
   - What we know: FeatureStore stores imbalance ratio but not absolute bid/ask depth. MarketSnapshot has spread_bps.
   - What's unclear: Whether spread_bps alone is a good enough depth proxy, or if we need imbalance variance as an additional signal.
   - Recommendation: Use spread_bps from MarketSnapshot as primary gate (e.g., spread > 20 bps = suppress). Add imbalance variance as a secondary check if initial testing shows false signals.

2. **Which strategies opt into funding filter first**
   - What we know: Correlation already has inline funding integration (Phase 3). Momentum and mean reversion are natural candidates per FUND-01.
   - What's unclear: Whether to refactor correlation to use the shared utility in Phase 4, or leave correlation's inline implementation and only add funding to momentum/mean_reversion.
   - Recommendation: Refactor correlation to use the shared utility (D-02 says "generalize the pattern"), then add to momentum and mean_reversion. This proves the utility works on the known-good case first.

3. **VWAP feasibility likelihood**
   - What we know: bar_volumes are np.diff of 24h rolling volume. Negative values are expected and documented.
   - What's unclear: The frequency and magnitude of negative values across instruments. If 50%+ of bars are negative, standard VWAP is not feasible.
   - Recommendation: Write the feasibility test first. If it fails, consider D-07 alternatives (rolling price-volume average without session resets, or TWAP). If all alternatives fail, defer per D-06.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | pyproject.toml (pytest section) |
| Quick run command | `.venv/bin/python -m pytest agents/signals/tests/ -x -q` |
| Full suite command | `.venv/bin/python -m pytest agents/signals/tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FUND-01 | Funding utility boosts conviction for aligned direction signals | unit | `.venv/bin/python -m pytest agents/signals/tests/test_funding_filter.py -x` | Wave 0 |
| FUND-02 | Z-score computation with sparse data guard | unit | `.venv/bin/python -m pytest agents/signals/tests/test_funding_filter.py::test_zscore -x` | Wave 0 |
| FUND-03 | Time-to-settlement decay increases near settlement | unit | `.venv/bin/python -m pytest agents/signals/tests/test_funding_filter.py::test_decay -x` | Wave 0 |
| OBI-01 | OBI strategy emits directional signals from imbalance | unit | `.venv/bin/python -m pytest agents/signals/tests/test_orderbook_imbalance.py -x` | Wave 0 |
| OBI-02 | Time-weighted imbalance over multiple samples | unit | `.venv/bin/python -m pytest agents/signals/tests/test_orderbook_imbalance.py::test_time_weighted -x` | Wave 0 |
| OBI-03 | Minimum depth gate suppresses thin book signals | unit | `.venv/bin/python -m pytest agents/signals/tests/test_orderbook_imbalance.py::test_depth_gate -x` | Wave 0 |
| OBI-04 | High conviction routes to Portfolio A | unit | `.venv/bin/python -m pytest agents/signals/tests/test_orderbook_imbalance.py::test_portfolio_a -x` | Wave 0 |
| VWAP-01 | Feasibility validation of volume-delta approximation | unit | `.venv/bin/python -m pytest agents/signals/tests/test_vwap.py::test_feasibility -x` | Wave 0 |
| VWAP-02 | Session VWAP computation with reset | unit | `.venv/bin/python -m pytest agents/signals/tests/test_vwap.py::test_session_vwap -x` | Wave 0 (if feasible) |
| VWAP-03 | Deviation-based signals from VWAP | unit | `.venv/bin/python -m pytest agents/signals/tests/test_vwap.py::test_deviation -x` | Wave 0 (if feasible) |
| VWAP-04 | Time-of-session awareness (later = more reliable) | unit | `.venv/bin/python -m pytest agents/signals/tests/test_vwap.py::test_session_time -x` | Wave 0 (if feasible) |

### Sampling Rate
- **Per task commit:** `.venv/bin/python -m pytest agents/signals/tests/ -x -q`
- **Per wave merge:** `.venv/bin/python -m pytest agents/signals/tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/signals/tests/test_funding_filter.py` -- covers FUND-01, FUND-02, FUND-03
- [ ] `agents/signals/tests/test_orderbook_imbalance.py` -- covers OBI-01, OBI-02, OBI-03, OBI-04
- [ ] `agents/signals/tests/test_vwap.py` -- covers VWAP-01 (feasibility), VWAP-02/03/04 (if feasible)

## Sources

### Primary (HIGH confidence)
- `agents/signals/strategies/correlation.py` -- Funding rate integration pattern (Phase 3), z-score computation, conviction model
- `agents/signals/strategies/momentum.py` -- Volume confirmation, 3-component conviction model, Portfolio A routing
- `agents/signals/strategies/base.py` -- SignalStrategy interface contract
- `agents/signals/feature_store.py` -- All available data (orderbook_imbalances, funding_rates, bar_volumes, timestamps, closes)
- `agents/signals/main.py` -- Strategy registration (STRATEGY_CLASSES, STRATEGY_PARAMS_CLASSES)
- `agents/ingestion/normalizer.py` -- How orderbook_imbalance and funding_rate are populated
- `agents/ingestion/enrichment.py` -- `compute_orderbook_imbalance`: (bid_vol - ask_vol) / (bid_vol + ask_vol)
- `libs/common/models/market_snapshot.py` -- Available fields (hours_since_last_funding, next_funding_time, spread_bps)
- `libs/common/models/enums.py` -- SignalSource.ORDERBOOK_IMBALANCE, SignalSource.VWAP already exist
- `configs/strategies/orderbook_imbalance.yaml` -- Existing config template (needs parameter updates)
- `configs/strategy_matrix.yaml` -- Strategy-instrument enablement matrix

### Secondary (MEDIUM confidence)
- Phase 3 decisions from STATE.md -- Funding rate direction semantics confirmed (positive=bearish, negative=bullish)
- CONTEXT.md decisions D-01 through D-11 -- User-locked design decisions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- all patterns directly observable in existing code, mechanical replication
- Pitfalls: HIGH -- sparse funding data and negative bar_volumes are documented in code comments and prior phase decisions

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- internal project, no external dependency changes)
