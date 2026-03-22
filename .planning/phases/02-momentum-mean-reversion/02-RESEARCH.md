# Phase 2: Momentum and Mean Reversion Improvements - Research

**Researched:** 2026-03-22
**Domain:** Trading strategy signal quality (momentum EMA crossover, mean reversion Bollinger Bands)
**Confidence:** HIGH

## Summary

Phase 2 modifies two existing strategy files (`momentum.py` and `mean_reversion.py`) plus their YAML configs. The work is entirely within the signal generation layer -- no new files needed except tests, no new dependencies. All required data (closes, highs, lows, volumes/bar_volumes, ATR) is already available in FeatureStore. The `realized_volatility()` function already exists in `libs/indicators/volatility.py` and can be used for the adaptive conviction model (MOM-02).

The main complexity is in the **conviction model redesign** for both strategies (volume confirmation, volatility-adaptive scaling, multi-factor trend rejection) and the **swing point detection** algorithm for momentum stops. These are self-contained numerical computations that can be tested deterministically. Portfolio A routing is mechanically simple -- conditional `suggested_target` assignment based on conviction threshold.

**Primary recommendation:** Implement each requirement as a focused modification to the existing `evaluate()` and `_compute_conviction()` methods, keeping all new parameters configurable via YAML. Fix the momentum YAML loader bug (D-07) first since all subsequent changes add new parameters.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Conviction thresholds for Portfolio A routing are at Claude's discretion -- pick values that make sense for the signal quality
- **D-02:** Momentum requires a higher conviction threshold for Portfolio A than mean reversion -- breakouts fail more often and need more confirmation
- **D-03:** Routing decision is conviction-only -- no per-instrument routing differences, keep it simple
- **D-04:** Position sizing and notional caps for Portfolio A signals are entirely the risk agent's responsibility -- strategies just set `suggested_target=PortfolioTarget.A` above the threshold
- **D-05:** Momentum gets a higher weight than mean reversion (currently 0.15) when re-enabled -- it's typically more frequent
- **D-06:** Enable momentum for all 5 instruments immediately -- no gradual rollout
- **D-07:** Fix the YAML loader bugs as part of Phase 2 -- missing fields (adx_threshold, adx_period, cooldown_bars, stop_loss_atr_mult, take_profit_atr_mult) must be loaded from config
- **D-08:** Defer per-instrument parameter tuning for momentum until the strategy logic stabilizes -- use reasonable defaults for now, but track tuning as a future task
- **D-09:** Set `take_profit` to the extended target price; note the partial exit level (band middle/mean) in signal metadata for the execution layer
- **D-10:** Definition of "strong" reversion and the extended target placement are at Claude's discretion -- pick what's most logical and likely to be profitable
- **D-11:** Uniform partial/extended target logic across all instruments -- let the per-instrument band widths (already tuned in Phase 1) handle differences naturally
- **D-12:** Reject momentum crossovers when bar_volume is below a rolling average -- not on any decline, but on significant underperformance vs recent average
- **D-13:** Rolling average window size is at Claude's discretion, but should be small (not a huge lookback)
- **D-14:** Volume should also boost conviction when surging -- not just filter on decline, but reward increasing volume with higher conviction scores
- **D-15:** Mean reversion also gets volume confirmation -- high volume on a band touch confirms reversion strength and should boost conviction

### Claude's Discretion
- Specific Portfolio A conviction thresholds for momentum and mean reversion (D-01, D-02)
- Momentum weight value (D-05 -- higher than 0.15, exact number TBD)
- Definition of "strong" reversion for extended targets (D-10)
- Extended target placement formula (D-10)
- Volume rolling average lookback window (D-13)
- Volume boost formula for conviction (D-14, D-15)
- Swing point detection algorithm for structure-aware stops (MOM-03)
- Multi-factor trend rejection formula for mean reversion (MR-01)
- Adaptive band width scaling approach (MR-02)

### Deferred Ideas (OUT OF SCOPE)
- Per-instrument momentum parameter tuning -- defer until strategy logic stabilizes, track as future task (D-08)
- Adaptive conviction thresholds scaling with volatility percentile -- Phase 5 (XQ-01) provides shared utility; Phase 2 implements MOM-02 inline
- Session-aware parameter profiles -- Phase 5 (XQ-02, XQ-03)
- Swing point detection as shared utility -- Phase 5 (XQ-05) may extract to reusable module; Phase 2 implements inline for momentum

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MOM-01 | Volume confirmation -- reject EMA crossovers when volume rate-of-change is declining | `FeatureStore.bar_volumes` already available; use rolling mean of bar_volumes as threshold; volume ratio also feeds conviction boost (D-14) |
| MOM-02 | Adaptive conviction model -- scale conviction with current vs historical volatility percentile | `realized_volatility()` exists in `libs/indicators/volatility.py`; compute rolling percentile rank of current ATR vs historical ATR window |
| MOM-03 | Structure-aware stop placement -- use recent swing high/low instead of fixed ATR multiples | `store.highs` and `store.lows` available; implement simple swing point detection (local extrema in rolling window) |
| MOM-04 | Portfolio A dual routing -- high-conviction breakout signals eligible for autonomous execution | Conditional `suggested_target=PortfolioTarget.A` when conviction >= threshold; D-02 requires momentum threshold > mean reversion |
| MR-01 | Multi-factor trend rejection -- EMA slope + consecutive closes + momentum strength, not just ADX | `ema()` and `rsi()` available in indicators lib; compute EMA slope, count consecutive directional closes, combine with existing ADX |
| MR-02 | Adaptive band width -- adjust Bollinger Band std multiplier based on volatility regime | `bollinger_bands()` already accepts `num_std` parameter; compute ATR percentile to scale `bb_std` dynamically |
| MR-03 | Improved take-profit targeting -- partial targets at mean, extended targets beyond for strong reversions | D-09: set `take_profit` to extended target, put partial level in `metadata["partial_target"]`; define "strong" as deviation > threshold |
| MR-04 | Portfolio A dual routing -- extreme deviation (3+ sigma) signals eligible for autonomous execution | Conditional `suggested_target=PortfolioTarget.A` when deviation >= 3 sigma equivalent AND conviction >= threshold |

</phase_requirements>

## Standard Stack

### Core (already in project -- no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| numpy | 1.26+ | Array math for indicators, percentile computation | Already used throughout indicators and strategies |
| libs/indicators/volatility.py | N/A | `realized_volatility()`, `atr()`, `bollinger_bands()` | Already implemented, tested |
| libs/indicators/oscillators.py | N/A | `adx()`, `rsi()` | Already implemented, tested |
| libs/indicators/moving_averages.py | N/A | `ema()`, `sma()` | Already implemented, tested |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| scipy | already installed (Phase 1) | `scipy.stats.percentileofscore` for volatility percentile | MOM-02 volatility percentile rank |

**No new dependencies required.** All computation uses numpy and existing indicator functions.

## Architecture Patterns

### Files Modified

```
agents/signals/strategies/
  momentum.py           # MOM-01..04: volume filter, adaptive conviction, swing stops, Portfolio A
  mean_reversion.py     # MR-01..04: multi-factor trend, adaptive bands, extended TP, Portfolio A
configs/strategies/
  momentum.yaml         # D-07: fix missing fields, enable, set weight, add new params
  mean_reversion.yaml   # Add new params (trend rejection factors, extended target config)
configs/strategy_matrix.yaml  # D-06: enable momentum for all 5 instruments
agents/signals/tests/
  test_momentum.py      # Extend with volume confirmation, adaptive conviction, swing stop tests
  test_mean_reversion.py # Extend with multi-factor, adaptive band, extended TP, Portfolio A tests
```

### Pattern 1: Volume Confirmation Filter (MOM-01)

**What:** Reject EMA crossovers when recent bar volume is significantly below its rolling average. Also boost conviction when volume is surging (D-14).
**When to use:** Applied in `evaluate()` after crossover detection, before conviction computation.

**Algorithm:**
```python
bar_vols = store.bar_volumes  # np.diff of 24h volume, length = sample_count - 1
if len(bar_vols) < vol_lookback:
    return []  # Not enough volume data

# Use absolute values -- bar_volumes can be negative when high-vol periods roll off
recent_vols = np.abs(bar_vols[-vol_lookback:])
vol_avg = np.mean(recent_vols)
cur_vol = np.abs(bar_vols[-1])

# Reject if volume is significantly below average (e.g., < 50% of rolling mean)
if vol_avg > 0 and cur_vol < vol_avg * vol_min_ratio:
    return []  # Low volume -- likely false breakout

# Compute volume ratio for conviction boost
volume_ratio = cur_vol / vol_avg if vol_avg > 0 else 1.0
```

**Recommended parameters:**
- `vol_lookback: int = 10` -- 10-bar rolling window (D-13: small, not huge)
- `vol_min_ratio: float = 0.5` -- reject below 50% of average (D-12: significant underperformance)

**Note on bar_volumes:** Values can be negative when high-volume periods roll off the 24h window. Use `np.abs()` for volume magnitude comparison. This is a known design decision from Phase 1.

### Pattern 2: Adaptive Conviction with Volatility Percentile (MOM-02)

**What:** Scale conviction based on where current volatility sits in its historical distribution. High-percentile volatility = breakouts more meaningful = higher conviction.
**When to use:** In `_compute_conviction()`, replace fixed ADX-only model.

**Algorithm:**
```python
from scipy.stats import percentileofscore

atr_vals = atr(highs, lows, closes, p.atr_period)
valid_atr = atr_vals[~np.isnan(atr_vals)]
if len(valid_atr) > 0:
    vol_percentile = percentileofscore(valid_atr, cur_atr) / 100.0  # 0.0-1.0
else:
    vol_percentile = 0.5  # Neutral fallback

# Scale conviction: low-vol breakouts get penalized, high-vol get boosted
# vol_percentile 0.0-1.0 maps to multiplier ~0.7-1.3
vol_multiplier = 0.7 + 0.6 * vol_percentile
```

**Conviction model redesign (3-component):**
- ADX component (0-0.35): trend strength
- RSI component (0-0.35): directional alignment
- Volume/volatility component (0-0.30): vol confirmation + volatility percentile

### Pattern 3: Swing Point Detection for Stops (MOM-03)

**What:** Find recent local highs/lows as stop-loss placement points instead of fixed ATR multiples.
**When to use:** After signal direction determined, before building `StandardSignal`.

**Algorithm:**
```python
def _find_swing_low(lows: NDArray, lookback: int = 20, order: int = 3) -> float | None:
    """Find most recent swing low (local minimum with `order` bars on each side)."""
    search = lows[-lookback:]
    for i in range(len(search) - 1 - order, order - 1, -1):
        if all(search[i] <= search[i - j] for j in range(1, order + 1)) and \
           all(search[i] <= search[i + j] for j in range(1, min(order + 1, len(search) - i))):
            return float(search[i])
    return None  # Fallback to ATR-based stop

def _find_swing_high(highs: NDArray, lookback: int = 20, order: int = 3) -> float | None:
    """Find most recent swing high (local maximum with `order` bars on each side)."""
    # Mirror of swing_low with >= comparisons
```

**Parameters:**
- `swing_lookback: int = 20` -- how far back to search for swing points
- `swing_order: int = 3` -- minimum bars on each side of a swing point

**Fallback:** If no swing point found within lookback, use ATR-based stop as before. This prevents the strategy from failing when markets are trending without clear pivots.

### Pattern 4: Multi-Factor Trend Rejection (MR-01)

**What:** Replace single ADX threshold with a composite score: EMA slope + consecutive directional closes + momentum strength.
**When to use:** In `evaluate()`, replacing the `if adx_valid and cur_adx > p.adx_max: return []` block.

**Algorithm:**
```python
def _compute_trend_strength(
    closes: NDArray, highs: NDArray, lows: NDArray,
    ema_period: int = 20, lookback: int = 5,
) -> float:
    """Compute composite trend strength 0.0-1.0. Higher = stronger trend = more rejection."""
    # Factor 1: EMA slope (normalized by ATR)
    ema_vals = ema(closes, ema_period)
    ema_slope = (ema_vals[-1] - ema_vals[-2]) / cur_atr  # ATR-normalized
    slope_score = min(abs(ema_slope) / 0.5, 1.0) * 0.4  # 0-0.4

    # Factor 2: Consecutive directional closes
    consecutive = 0
    for i in range(len(closes) - 1, max(len(closes) - lookback - 1, 0), -1):
        if (closes[i] > closes[i-1]) == (closes[-1] > closes[-2]):
            consecutive += 1
        else:
            break
    consec_score = min(consecutive / lookback, 1.0) * 0.3  # 0-0.3

    # Factor 3: ADX (if valid)
    adx_score = min(cur_adx / 50.0, 1.0) * 0.3 if adx_valid else 0.15  # 0-0.3

    return slope_score + consec_score + adx_score
```

**Threshold:** Reject when `trend_strength > trend_reject_threshold` (configurable, default 0.6). This replaces the single `adx_max` check.

### Pattern 5: Adaptive Band Width (MR-02)

**What:** Scale Bollinger Band `num_std` multiplier based on volatility regime. In low-vol, tighten bands (lower multiplier) to catch smaller deviations. In high-vol, widen bands to avoid noise.
**When to use:** In `evaluate()`, before calling `bollinger_bands()`.

**Algorithm:**
```python
# Compute ATR percentile for volatility regime
atr_vals = atr(highs, lows, closes, p.atr_period)
valid_atr = atr_vals[~np.isnan(atr_vals)]
if len(valid_atr) >= 20:
    vol_pct = percentileofscore(valid_atr, cur_atr) / 100.0
else:
    vol_pct = 0.5

# Scale bb_std: base_std * (0.8 + 0.4 * vol_pct)
# Low vol (pct=0.1): 0.84x base  -> tighter bands, catch smaller moves
# High vol (pct=0.9): 1.16x base -> wider bands, filter noise
adaptive_std = p.bb_std * (0.8 + 0.4 * vol_pct)
bb = bollinger_bands(closes, p.bb_period, adaptive_std)
```

### Pattern 6: Extended Take-Profit Targets (MR-03)

**What:** For strong reversions, set take_profit beyond the middle band. Place partial target at middle band in metadata.
**When to use:** After signal generated, when computing take_profit.

**Algorithm:**
```python
# "Strong" reversion: deviation beyond band is > extended_deviation_threshold
extended_deviation_threshold = 0.5  # 50% of band width beyond the band

if deviation > extended_deviation_threshold:
    # Extended target: middle band + fraction of the opposite band distance
    if direction == PositionSide.LONG:
        extended_target = round_to_tick(middle_d + (middle_d - lower_d) * Decimal("0.5"))
    else:
        extended_target = round_to_tick(middle_d - (upper_d - middle_d) * Decimal("0.5"))
    take_profit = extended_target
    partial_target = round_to_tick(middle_d)
else:
    take_profit = round_to_tick(middle_d)
    partial_target = None

# D-09: Set take_profit to extended, note partial in metadata
metadata["partial_target"] = str(partial_target) if partial_target else None
```

### Pattern 7: Portfolio A Routing (MOM-04, MR-04)

**What:** Set `suggested_target=PortfolioTarget.A` when conviction exceeds a strategy-specific threshold.
**When to use:** After conviction computed, when building `StandardSignal`.

**Recommended thresholds (Claude's discretion per D-01, D-02):**
- **Momentum:** `portfolio_a_min_conviction = 0.75` -- breakouts fail more often, need strong confirmation
- **Mean Reversion:** `portfolio_a_min_conviction = 0.65` -- extreme band touches (3+ sigma) with RSI confirmation are higher probability

```python
if conviction >= p.portfolio_a_min_conviction:
    suggested_target = PortfolioTarget.A
else:
    suggested_target = PortfolioTarget.B
```

### Anti-Patterns to Avoid

- **Hardcoding thresholds:** Every new threshold must be a field in the params dataclass AND loaded from YAML config. No magic numbers in logic.
- **Breaking existing tests:** The conviction model changes will invalidate existing test assertions about `suggested_target == PortfolioTarget.B`. Update tests to expect conditional routing.
- **Mixing float and Decimal:** Keep all price/stop/TP computations in Decimal. Volume/ATR percentile calculations stay in float (numpy domain).
- **Using bar_volumes without abs():** `bar_volumes` can be negative -- always use `np.abs()` for magnitude comparisons.
- **Ignoring NaN in new computations:** All new indicator computations must check for NaN values before using them. The existing pattern of `if np.isnan(v): return []` must be extended.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Volatility percentile | Custom percentile ranking | `scipy.stats.percentileofscore` | Handles edge cases (ties, empty arrays) |
| Realized volatility | Manual log-return stdev | `libs/indicators/volatility.realized_volatility()` | Already implemented and tested |
| EMA computation | Manual exponential smoothing | `libs/indicators/moving_averages.ema()` | Already implemented with proper seeding |
| Bollinger Bands | Manual SMA +/- std | `libs/indicators/volatility.bollinger_bands()` | Returns structured result with bandwidth |

**Key insight:** All required indicator functions already exist in the project. Phase 2 composes existing indicators into smarter decision logic -- it does not need new indicator implementations.

## Common Pitfalls

### Pitfall 1: bar_volumes Length Mismatch
**What goes wrong:** `bar_volumes` has length `sample_count - 1` (it's `np.diff`), so indexing must account for offset.
**Why it happens:** `store.closes` has N elements, `store.bar_volumes` has N-1.
**How to avoid:** When comparing volume at bar index `i`, use `bar_volumes[i-1]` or simply use `bar_volumes[-vol_lookback:]` for rolling window.
**Warning signs:** IndexError or off-by-one in volume confirmation.

### Pitfall 2: Momentum YAML Loader Missing Fields
**What goes wrong:** The `__init__` config parser only reads 7 of 12 `MomentumParams` fields. Missing: `adx_threshold`, `adx_period`, `cooldown_bars`, `stop_loss_atr_mult`, `take_profit_atr_mult`.
**Why it happens:** Loader was written before all params were finalized; was never updated.
**How to avoid:** D-07 requires fixing this first. Add all missing `p.get()` calls to the `__init__` config parser.
**Warning signs:** Config changes to these fields have no effect at runtime.

### Pitfall 3: Mean Reversion Config Also Missing Fields
**What goes wrong:** Mean reversion config loader is missing `atr_period`, `stop_loss_atr_mult`, and `cooldown_bars` from its `p.get()` calls.
**Why it happens:** Same bug pattern as momentum.
**How to avoid:** Fix both loaders in the same task.
**Warning signs:** `atr_period=14` default used regardless of YAML setting.

### Pitfall 4: Swing Point Not Found Fallback
**What goes wrong:** In strongly trending markets, there may be no valid swing point within the lookback window.
**Why it happens:** All recent bars move monotonically.
**How to avoid:** Always have an ATR-based fallback stop. Never return `None` as a stop_loss.
**Warning signs:** Strategy crashes or emits signals without stops.

### Pitfall 5: Conviction Exceeds 1.0 After Redesign
**What goes wrong:** Adding a volume component to the conviction model can push total above 1.0.
**Why it happens:** Three components each contribute up to their max, and multipliers stack.
**How to avoid:** Always `min(total, 1.0)` and `max(total, 0.0)` at the end. The `StandardSignal.__post_init__` validates `0 <= conviction <= 1.0`.
**Warning signs:** `ValueError: Conviction must be in [0, 1]` at signal construction.

### Pitfall 6: Strategy Matrix Not Updated
**What goes wrong:** Momentum is enabled in momentum.yaml but still disabled in strategy_matrix.yaml.
**Why it happens:** Two separate enablement controls.
**How to avoid:** D-06 requires updating `strategy_matrix.yaml` to enable momentum for all 5 instruments.
**Warning signs:** Momentum strategy never instantiated despite config changes.

## Code Examples

### Momentum YAML Loader Fix (D-07)

Current broken loader (missing 5 fields):
```python
# Current -- BROKEN: missing adx_threshold, adx_period, cooldown_bars,
# stop_loss_atr_mult, take_profit_atr_mult
self._params = MomentumParams(
    fast_ema_period=p.get("fast_ema_period", ...),
    slow_ema_period=p.get("slow_ema_period", ...),
    rsi_period=p.get("rsi_period", ...),
    rsi_overbought=p.get("rsi_overbought", ...),
    rsi_oversold=p.get("rsi_oversold", ...),
    atr_period=p.get("atr_period", ...),
    min_conviction=p.get("min_conviction", ...),
)
```

Fixed loader (all 12 fields):
```python
self._params = MomentumParams(
    fast_ema_period=p.get("fast_ema_period", self._params.fast_ema_period),
    slow_ema_period=p.get("slow_ema_period", self._params.slow_ema_period),
    adx_period=p.get("adx_period", self._params.adx_period),
    adx_threshold=p.get("adx_threshold", self._params.adx_threshold),
    rsi_period=p.get("rsi_period", self._params.rsi_period),
    rsi_overbought=p.get("rsi_overbought", self._params.rsi_overbought),
    rsi_oversold=p.get("rsi_oversold", self._params.rsi_oversold),
    atr_period=p.get("atr_period", self._params.atr_period),
    stop_loss_atr_mult=p.get("stop_loss_atr_mult", self._params.stop_loss_atr_mult),
    take_profit_atr_mult=p.get("take_profit_atr_mult", self._params.take_profit_atr_mult),
    min_conviction=p.get("min_conviction", self._params.min_conviction),
    cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
)
```

### Conviction Boost from Volume (D-14)

```python
# Volume ratio feeds into conviction as a third component
# volume_ratio = cur_vol / vol_avg (already computed in volume filter)
# ratio > 1.0 means above-average volume (boost), < 1.0 means below (penalize)
vol_score = min(max((volume_ratio - 0.5) / 2.0, 0.0), 0.30)
# volume_ratio=0.5 -> 0.0 score
# volume_ratio=1.0 -> 0.125 score
# volume_ratio=1.5 -> 0.25 score
# volume_ratio=2.5+ -> 0.30 score (capped)
```

### Momentum Weight (D-05)

```yaml
# momentum.yaml -- D-05: higher weight than mean_reversion (0.15)
strategy:
  name: "momentum"
  enabled: true
  weight: 0.20  # Higher than MR's 0.15; most frequent strategy
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Momentum disabled globally | Re-enable for all 5 instruments | Phase 2 (D-06) | Doubles active strategy count for signal coverage |
| Fixed ADX-only trend filter (MR) | Multi-factor trend rejection | Phase 2 (MR-01) | Fewer false rejections in choppy-but-mean-reverting markets |
| Fixed ATR stops (momentum) | Swing point stops with ATR fallback | Phase 2 (MOM-03) | Stops at structural levels rather than arbitrary multiples |
| Fixed conviction model | Volatility-adaptive + volume-boosted | Phase 2 (MOM-02, D-14) | Higher conviction in high-vol breakouts, lower in low-vol noise |
| Take profit at middle band only (MR) | Extended targets for strong reversions | Phase 2 (MR-03) | Captures more upside on extreme deviations |
| All signals route to Portfolio B | Conditional Portfolio A routing | Phase 2 (MOM-04, MR-04) | Best signals execute autonomously |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py agents/signals/tests/test_mean_reversion.py -x -q` |
| Full suite command | `.venv/bin/python -m pytest agents/signals/tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MOM-01 | Volume filter rejects low-volume crossovers | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumVolumeFilter -x` | Wave 0 |
| MOM-01 | Volume filter passes high-volume crossovers | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumVolumeFilter -x` | Wave 0 |
| MOM-02 | Conviction scales with volatility percentile | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumAdaptiveConviction -x` | Wave 0 |
| MOM-03 | Stop placed at swing low/high when available | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumSwingStops -x` | Wave 0 |
| MOM-03 | ATR fallback when no swing point found | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumSwingStops -x` | Wave 0 |
| MOM-04 | High-conviction signals route to Portfolio A | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumPortfolioRouting -x` | Wave 0 |
| MR-01 | Multi-factor trend rejection blocks trending signals | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRTrendRejection -x` | Wave 0 |
| MR-01 | Multi-factor trend rejection allows choppy-but-reverting | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRTrendRejection -x` | Wave 0 |
| MR-02 | Band width adapts to volatility regime | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRAdaptiveBands -x` | Wave 0 |
| MR-03 | Strong reversions get extended take-profit | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRExtendedTargets -x` | Wave 0 |
| MR-03 | Partial target in metadata for extended signals | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRExtendedTargets -x` | Wave 0 |
| MR-04 | Extreme deviation signals route to Portfolio A | unit | `.venv/bin/python -m pytest agents/signals/tests/test_mean_reversion.py::TestMRPortfolioRouting -x` | Wave 0 |
| D-07 | YAML loader reads all momentum params | unit | `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py::TestMomentumConfig -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `.venv/bin/python -m pytest agents/signals/tests/test_momentum.py agents/signals/tests/test_mean_reversion.py -x -q`
- **Per wave merge:** `.venv/bin/python -m pytest agents/signals/tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/signals/tests/test_momentum.py::TestMomentumVolumeFilter` -- covers MOM-01
- [ ] `agents/signals/tests/test_momentum.py::TestMomentumAdaptiveConviction` -- covers MOM-02
- [ ] `agents/signals/tests/test_momentum.py::TestMomentumSwingStops` -- covers MOM-03
- [ ] `agents/signals/tests/test_momentum.py::TestMomentumPortfolioRouting` -- covers MOM-04
- [ ] `agents/signals/tests/test_momentum.py::TestMomentumConfig` -- covers D-07
- [ ] `agents/signals/tests/test_mean_reversion.py::TestMRTrendRejection` -- covers MR-01
- [ ] `agents/signals/tests/test_mean_reversion.py::TestMRAdaptiveBands` -- covers MR-02
- [ ] `agents/signals/tests/test_mean_reversion.py::TestMRExtendedTargets` -- covers MR-03
- [ ] `agents/signals/tests/test_mean_reversion.py::TestMRPortfolioRouting` -- covers MR-04
- [ ] Update existing tests that assert `suggested_target == PortfolioTarget.B` to handle conditional routing

## Open Questions

1. **Volume data quality for bar_volumes**
   - What we know: `bar_volumes` is `np.diff(volumes)` where `volumes` is 24h rolling. Values can be negative.
   - What's unclear: How stable the volume signal is in practice -- rolling 24h volume diffs may be noisy.
   - Recommendation: Use `np.abs()` for magnitude and keep the volume filter ratio conservative (0.5x threshold). The volume boost for conviction is multiplicative, not binary, so noise gets dampened.

2. **Mean reversion YAML loader also has missing fields**
   - What we know: `atr_period`, `stop_loss_atr_mult`, and `cooldown_bars` are present in the params dataclass but the YAML already includes them and the loader reads `atr_period` via... wait -- checking code: the MR loader is ALSO missing `atr_period`, `stop_loss_atr_mult`, and `cooldown_bars` from its `p.get()` calls.
   - Recommendation: Fix both loaders in the same task as D-07.

## Sources

### Primary (HIGH confidence)
- `agents/signals/strategies/momentum.py` -- full current implementation reviewed, line-by-line
- `agents/signals/strategies/mean_reversion.py` -- full current implementation reviewed
- `agents/signals/feature_store.py` -- confirmed bar_volumes, highs, lows availability
- `libs/indicators/volatility.py` -- confirmed `realized_volatility()`, `atr()`, `bollinger_bands()` signatures
- `libs/indicators/oscillators.py` -- confirmed `adx()`, `rsi()` signatures
- `libs/indicators/moving_averages.py` -- confirmed `ema()`, `sma()` signatures
- `configs/strategies/momentum.yaml` -- confirmed missing fields, disabled state
- `configs/strategies/mean_reversion.yaml` -- confirmed per-instrument overrides from Phase 1
- `configs/strategy_matrix.yaml` -- confirmed momentum disabled
- `libs/common/models/signal.py` -- confirmed StandardSignal fields including metadata dict
- `agents/signals/main.py` -- confirmed strategy loading, config validation, matrix integration

### Secondary (MEDIUM confidence)
- Swing point detection algorithm -- standard technical analysis approach (local extrema); implementation is straightforward numpy
- Volume confirmation filtering -- well-established trading concept; specific thresholds (0.5x ratio, 10-bar window) are reasonable starting points

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project, no new dependencies
- Architecture: HIGH -- all changes are within existing strategy files, no new abstractions needed
- Pitfalls: HIGH -- identified from direct code review, known bugs documented in CONTEXT.md
- Algorithm design: MEDIUM -- specific parameter values (conviction thresholds, volume ratios) are reasonable estimates that may need runtime tuning

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- modifying existing code, no external dependency risk)
