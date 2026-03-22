# Phase 3: Liquidation, Correlation, and Regime Improvements - Research

**Researched:** 2026-03-22
**Domain:** Trading strategy enhancement (liquidation cascade, correlation, regime trend)
**Confidence:** HIGH

## Summary

Phase 3 modifies three existing strategies that already have working implementations, per-instrument configs (from Phase 1), and test suites. The changes follow well-established patterns from Phase 2 (momentum and mean reversion improvements): volume confirmation gates, multi-factor conviction models, adaptive parameter scaling via `scipy.stats.percentileofscore`, and Portfolio A routing via conviction thresholds.

The liquidation cascade strategy needs graduated tiers (mild/moderate/severe OI drops) with tier-specific position sizing and stop widths, plus volume surge confirmation. The correlation strategy needs multi-window basis analysis (short/medium/long lookbacks) with funding rate as a third factor, plus Portfolio A routing. The regime trend strategy needs adaptive ADX and ATR expansion thresholds based on volatility regime, plus trailing stop metadata in signals.

**Primary recommendation:** Follow Phase 2's proven pattern exactly -- add new params to the dataclass, add new logic to evaluate(), update conviction model, update YAML configs, write tests first. All data needed (open_interests, bar_volumes, funding_rates, closes, highs, lows) is already available in FeatureStore.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Aggressive position sizing on severe cascades -- tier 3 (>8% OI drop) should take larger positions with wider stops, not conservative across all tiers
- **D-02:** Tier boundaries as specified in requirements: Tier 1 (2-4% OI drop), Tier 2 (4-8%), Tier 3 (>8%)
- **D-03:** Volume surge confirmation required alongside OI drops to distinguish forced liquidation from organic reduction (LIQ-02)
- **D-04:** Liquidation cascade remains disabled for QQQ/SPY (Phase 1, D-11 -- crypto-native strategy)
- **D-05:** Fire when 2 of 3 windows agree, provided funding rate favors the same direction -- funding acts as a confirming tiebreaker, not a standalone trigger
- **D-06:** If all 3 windows agree, fire regardless of funding rate direction -- unanimous basis agreement is strong enough on its own
- **D-07:** Funding rate integration creates a three-factor model: short/medium/long basis windows + funding rate direction alignment
- **D-08:** Full discretion to Claude on trailing stop metadata design -- trail parameters, initial stop tightness, and metadata format
- **D-09:** Adaptive ADX and ATR expansion thresholds adjust with volatility regime -- implementation details at Claude's discretion
- **D-10:** High-conviction correlation signals route to Portfolio A -- follows the pattern established in Phase 2 (conviction threshold -> suggested_target=PortfolioTarget.A)
- **D-11:** Portfolio A conviction threshold for correlation at Claude's discretion -- should reflect the multi-window + funding agreement quality

### Claude's Discretion
- Exact position sizing multipliers per liquidation tier (D-01 -- aggressive on severe)
- Stop width multipliers per liquidation tier
- Volume surge threshold for liquidation confirmation (D-03)
- Correlation Portfolio A conviction threshold (D-11)
- How funding rate weight interacts with basis window agreement (D-05, D-07)
- Trailing stop trail parameters and metadata format (D-08)
- Adaptive threshold scaling formulas for regime trend (D-09)
- Whether regime trend also gets Portfolio A routing (not in requirements -- leave as-is if not)

### Deferred Ideas (OUT OF SCOPE)
- Regime trend Portfolio A routing -- not in Phase 3 requirements, could be added in Phase 5 cross-cutting quality
- Liquidation cascade for equity perps -- deferred indefinitely (crypto-native pattern)
- Shared swing point detection utility -- Phase 5 (XQ-05) may extract from momentum's inline implementation

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| LIQ-01 | Graduated response levels -- Tier 1 (mild OI drop 2-4%), Tier 2 (moderate 4-8%), Tier 3 (severe >8%) with different position sizing and stop widths | Tiered params in dataclass, tier detection in evaluate(), tier-specific stop/TP multipliers and position sizing metadata |
| LIQ-02 | Volume surge confirmation -- require volume spike alongside OI drop to distinguish forced liquidation from organic OI reduction | bar_volumes from FeatureStore, rolling average comparison pattern from momentum (MOM-01) |
| CORR-01 | Multi-window basis analysis -- short (30 bars), medium (60 bars), long (120 bars) lookback windows; signal fires when multiple agree | Compute basis z-score at three lookbacks, count agreements, apply D-05/D-06 agreement rules |
| CORR-02 | Funding rate integration -- extreme funding + extreme basis = higher conviction; create three-factor model | FeatureStore.funding_rates available, z-score computation already exists in correlation strategy |
| CORR-03 | Portfolio A dual routing -- multi-window + funding agreement signals eligible for autonomous execution | Follow momentum/mean_reversion Portfolio A pattern: conviction threshold -> suggested_target=PortfolioTarget.A |
| RT-01 | Adaptive filter thresholds -- ADX and ATR expansion thresholds adjust with volatility regime | Use percentileofscore on ATR history (Phase 2 pattern), scale ADX threshold and ATR expansion threshold inversely/proportionally |
| RT-02 | Dynamic trailing stop concept -- emit tighter initial stop with metadata suggesting trail parameters for execution layer | Add trail_atr_mult, trail_activation_atr_mult to metadata dict; tighter initial stop_loss_atr_mult |

</phase_requirements>

## Standard Stack

### Core (already installed, no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| numpy | 1.26+ | Array operations for OI/price/volume analysis | Already used in all strategies |
| scipy | Installed in Phase 1 | `percentileofscore` for adaptive thresholds (RT-01) | Already used in Phase 2 momentum/MR |

### Supporting (already available)

| Library | Purpose | Used By |
|---------|---------|---------|
| `libs/indicators/volatility.py` | ATR computation | All three strategies |
| `libs/indicators/oscillators.py` | ADX computation | Regime trend |
| `libs/indicators/moving_averages.py` | EMA, SMA computation | Regime trend |

**No new dependencies required.** All libraries needed are already installed and imported.

## Architecture Patterns

### Strategy Modification Pattern (established in Phases 1 and 2)

Each strategy follows identical structure:
```
agents/signals/strategies/<name>.py  -- Strategy implementation
configs/strategies/<name>.yaml       -- Default + per-instrument configs
agents/signals/tests/test_<name>.py  -- Test suite
```

### Pattern 1: Tiered Response (LIQ-01)
**What:** Classify OI drop magnitude into tiers with different parameters per tier.
**When to use:** When the same signal type has graduated severity levels requiring different risk responses.
**Implementation approach:**
```python
# In LiquidationCascadeParams, add tier-specific multipliers:
tier1_stop_atr_mult: float = 1.5      # Tighter stops for mild events
tier1_tp_atr_mult: float = 2.0
tier2_stop_atr_mult: float = 2.0      # Medium stops
tier2_tp_atr_mult: float = 3.0
tier3_stop_atr_mult: float = 3.0      # Wider stops for severe cascades (D-01: aggressive)
tier3_tp_atr_mult: float = 4.0

# In evaluate(), classify the tier:
def _classify_tier(oi_change_pct: float, params) -> int:
    """Tier 1: 2-4%, Tier 2: 4-8%, Tier 3: >8% OI drop."""
    abs_drop = abs(oi_change_pct)
    if abs_drop >= 8.0:
        return 3
    elif abs_drop >= 4.0:
        return 2
    else:
        return 1

# Tier in metadata for downstream consumers:
metadata={"tier": tier, "oi_change_pct": ..., ...}
```

### Pattern 2: Volume Surge Confirmation (LIQ-02)
**What:** Require elevated bar volume alongside OI drop to confirm forced liquidation.
**When to use:** When distinguishing forced vs organic market activity.
**Implementation approach (from momentum MOM-01):**
```python
# Uses store.bar_volumes -- same pattern as momentum strategy
bar_vols = store.bar_volumes
if len(bar_vols) < vol_lookback:
    return []
recent_vols = np.abs(bar_vols[-vol_lookback:])
vol_avg = float(np.mean(recent_vols))
cur_vol = float(np.abs(bar_vols[-1]))
# Require volume ABOVE average (surge), opposite of momentum which rejects below
if vol_avg > 0 and cur_vol < vol_avg * vol_surge_min_ratio:
    return []  # No volume surge = likely organic OI reduction
```

### Pattern 3: Multi-Window Agreement (CORR-01)
**What:** Compute basis z-scores at three lookback windows; require multi-window agreement.
**When to use:** When single-window analysis is noisy; multi-window consensus improves signal quality.
**Implementation approach:**
```python
# Three lookback windows (configurable via YAML)
short_lookback: int = 30
medium_lookback: int = 60
long_lookback: int = 120

# Compute z-scores at each window
z_short = self._compute_zscore(current_basis, basis_series, short_lookback)
z_medium = self._compute_zscore(current_basis, basis_series, medium_lookback)
z_long = self._compute_zscore(current_basis, basis_series, long_lookback)

# Count agreements (same direction, above threshold)
threshold = self._params.basis_zscore_threshold
agreements = sum(1 for z in [z_short, z_medium, z_long] if abs(z) >= threshold)
direction_agrees = all same sign among triggered windows

# D-06: all 3 agree -> fire regardless of funding
# D-05: 2 of 3 agree -> require funding rate confirmation
```

### Pattern 4: Funding Rate Integration (CORR-02)
**What:** Use funding rate direction as a third confirming factor for correlation signals.
**When to use:** When basis divergence is detected; funding alignment boosts conviction.
**Implementation approach:**
```python
# Funding rate from FeatureStore
funding_rates = store.funding_rates
if len(funding_rates) > 0:
    cur_funding = funding_rates[-1]
    # Positive funding -> longs pay shorts -> bearish pressure
    # Negative funding -> shorts pay longs -> bullish pressure
    funding_favors_long = cur_funding < 0
    funding_favors_short = cur_funding > 0

# D-05: 2-window agreement requires funding to confirm
# D-06: 3-window agreement fires regardless
if agreements == 3:
    pass  # Fire signal
elif agreements == 2 and funding_confirms_direction:
    pass  # Fire signal
else:
    return []  # Insufficient agreement
```

### Pattern 5: Adaptive Thresholds (RT-01)
**What:** Scale ADX and ATR expansion thresholds based on current volatility regime.
**When to use:** When fixed thresholds are too strict in low-vol or too loose in high-vol.
**Implementation approach (from Phase 2 percentileofscore pattern):**
```python
from scipy.stats import percentileofscore

# Compute volatility percentile
valid_atr = atr_vals[~np.isnan(atr_vals)]
if len(valid_atr) > 0:
    vol_pct = float(percentileofscore(valid_atr, cur_atr)) / 100.0
else:
    vol_pct = 0.5

# Low vol regime -> lower ADX threshold (easier to enter trends)
# High vol regime -> higher ADX threshold (require stronger trend confirmation)
adx_threshold = p.adx_threshold * (0.8 + 0.4 * vol_pct)
# Low vol -> lower ATR expansion threshold; high vol -> higher
atr_expansion = p.atr_expansion_threshold * (0.85 + 0.3 * vol_pct)
```

### Pattern 6: Trailing Stop Metadata (RT-02)
**What:** Emit trail parameters in signal metadata for the execution layer.
**When to use:** When strategy wants to suggest dynamic stop behavior without modifying execution layer.
**Implementation approach:**
```python
# Tighter initial stop (closer to entry than current fixed ATR mult)
# Trail parameters in metadata for execution layer to consume (ADV-02 in v2)
metadata={
    ...existing fields...,
    "trail_enabled": True,
    "trail_activation_pct": 1.0,   # Activate trailing after 1% profit
    "trail_distance_atr": 1.5,     # Trail at 1.5x ATR behind price
    "initial_stop_tightened": True, # Flag that stop is tighter than normal
}
```

### Pattern 7: Portfolio A Routing (CORR-03)
**What:** Route high-conviction signals to Portfolio A for autonomous execution.
**When to use:** When signal quality justifies autonomous trading without confirmation.
**Implementation approach (from Phase 2 momentum/MR):**
```python
# In params:
portfolio_a_min_conviction: float = 0.70

# In evaluate():
suggested_target = (
    PortfolioTarget.A
    if conviction >= p.portfolio_a_min_conviction
    else PortfolioTarget.B
)
```

### Anti-Patterns to Avoid
- **Separate strategy classes per tier:** Do NOT create Tier1Strategy, Tier2Strategy, etc. -- tiers are classified within the single evaluate() method.
- **Hardcoded magic numbers:** All thresholds, multipliers, and window sizes MUST be in the params dataclass and configurable via YAML.
- **Modifying FeatureStore:** No changes to data pipeline -- use existing properties (open_interests, bar_volumes, funding_rates, closes, etc.).
- **Changing StandardSignal contract:** Trailing stop metadata goes in the `metadata` dict, not as new fields on StandardSignal.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Z-score computation | Custom z-score | Existing `_compute_zscore()` in CorrelationStrategy | Already tested, handles edge cases (low std, short series) |
| ATR/ADX computation | Custom indicators | `libs/indicators/volatility.atr`, `libs/indicators/oscillators.adx` | Verified implementations, NaN handling |
| Volatility percentile | Custom ranking | `scipy.stats.percentileofscore` | Proven in Phase 2, handles edge cases |
| Volume confirmation | New volume analysis | `store.bar_volumes` + rolling average pattern from momentum | Established and tested pattern |
| Portfolio A routing | Complex routing logic | Simple conviction threshold comparison | Established pattern in 3 strategies already |

**Key insight:** Every feature in Phase 3 has a direct precedent in Phase 2 code. The novel part is combining these patterns into the three target strategies, not inventing new patterns.

## Common Pitfalls

### Pitfall 1: Funding Rate Array Length Mismatch
**What goes wrong:** Funding rates are sampled differently from prices -- only appended when funding_rate changes, not on every bar. The funding_rates array can be shorter than closes.
**Why it happens:** FeatureStore only appends a new funding rate when `snapshot.funding_rate != 0` AND differs from the previous value.
**How to avoid:** Always check `len(store.funding_rates) > 0` before using. Do NOT assume funding_rates has the same length as closes. Use the latest funding rate value, not try to index it at the same position as closes.
**Warning signs:** IndexError or empty array when accessing funding_rates.

### Pitfall 2: Tier Boundary Off-by-One
**What goes wrong:** OI drop of exactly 4.0% or 8.0% falls into wrong tier.
**Why it happens:** Ambiguous boundary conditions (>= vs >).
**How to avoid:** Use D-02 boundaries explicitly: Tier 1 is [2%, 4%), Tier 2 is [4%, 8%), Tier 3 is [8%, +inf). The existing `oi_drop_threshold_pct` (2.0) serves as the minimum for any tier to fire.
**Warning signs:** Test cases at exact boundaries failing.

### Pitfall 3: Multi-Window min_history Increase
**What goes wrong:** Correlation strategy now needs 120+ bars of history (long window) instead of 60, causing it to never fire on startup.
**Why it happens:** min_history property must account for the longest lookback window.
**How to avoid:** Update `min_history` property to return `max(long_lookback, ...) + buffer`. Document that the strategy will take ~2 hours of data collection (120 bars at 60s intervals) before first signal.
**Warning signs:** Strategy consistently returns [] in testing.

### Pitfall 4: Volume Surge vs Volume Filter Confusion
**What goes wrong:** Using the momentum volume filter pattern backwards -- momentum REJECTS low volume, but liquidation cascade should REQUIRE high volume (surge).
**Why it happens:** Copy-pasting momentum pattern without inverting the comparison.
**How to avoid:** Liquidation needs `cur_vol > vol_avg * vol_surge_min_ratio` (reject when volume is TOO LOW relative to average), which is the same direction as momentum but with a higher threshold (e.g., 1.5x instead of 0.5x).
**Warning signs:** Strategy fires on low-volume organic OI changes.

### Pitfall 5: Adaptive Threshold Drift
**What goes wrong:** Adaptive ADX/ATR thresholds become so permissive in low-vol that bad signals fire, or so strict in high-vol that no signals fire.
**Why it happens:** Unbounded scaling formula.
**How to avoid:** Clamp adaptive thresholds to a reasonable range (e.g., ADX 15-35, ATR expansion 0.8-1.5). The scaling formula should have min/max bounds.
**Warning signs:** Signal frequency dramatically changes between volatile and calm periods.

### Pitfall 6: Conviction Model Incoherence Across Tiers
**What goes wrong:** Tier 1 (mild) signals have higher conviction than Tier 3 (severe) because the conviction model doesn't account for tier.
**Why it happens:** If conviction is computed independently of tier, the OI-drop-based component may max out early.
**How to avoid:** Include tier as a factor in conviction -- Tier 3 should produce baseline higher conviction than Tier 1 for the same relative inputs.
**Warning signs:** Tier 1 signals routing to Portfolio A more often than Tier 3.

## Code Examples

### Liquidation Cascade: Tier Classification
```python
# New params for tiered response
@dataclass
class LiquidationCascadeParams:
    # ...existing params...
    # Tier boundaries (D-02)
    tier1_min_oi_drop_pct: float = 2.0   # Replaces oi_drop_threshold_pct
    tier2_min_oi_drop_pct: float = 4.0
    tier3_min_oi_drop_pct: float = 8.0
    # Tier-specific risk params (D-01: aggressive on severe)
    tier1_stop_atr_mult: float = 1.5
    tier1_tp_atr_mult: float = 2.0
    tier2_stop_atr_mult: float = 2.0
    tier2_tp_atr_mult: float = 3.0
    tier3_stop_atr_mult: float = 3.0    # Widest stops
    tier3_tp_atr_mult: float = 4.5      # Biggest targets
    # Volume surge confirmation (LIQ-02)
    vol_lookback: int = 10
    vol_surge_min_ratio: float = 1.5     # Require 1.5x average volume
```

### Correlation: Multi-Window + Funding Factor Model
```python
# New params for multi-window + funding
@dataclass
class CorrelationParams:
    # ...existing params...
    # Multi-window lookbacks (CORR-01)
    basis_short_lookback: int = 30
    basis_medium_lookback: int = 60
    basis_long_lookback: int = 120
    # Funding integration (CORR-02)
    funding_rate_boost: float = 0.10     # Conviction boost when funding agrees
    # Portfolio A routing (CORR-03)
    portfolio_a_min_conviction: float = 0.70
```

### Regime Trend: Adaptive Thresholds + Trail Metadata
```python
# New params for adaptive thresholds
@dataclass
class RegimeTrendParams:
    # ...existing params...
    # Adaptive threshold scaling (RT-01)
    adx_adapt_enabled: bool = True
    adx_adapt_low_mult: float = 0.8    # Multiplier at low vol
    adx_adapt_high_mult: float = 1.2   # Multiplier at high vol
    atr_expand_adapt_low_mult: float = 0.85
    atr_expand_adapt_high_mult: float = 1.15
    # Trail metadata (RT-02)
    trail_enabled: bool = True
    trail_activation_pct: float = 1.0   # Activate after 1% profit
    trail_distance_atr: float = 1.5     # Trail at 1.5x ATR
    initial_stop_atr_mult: float = 1.8  # Tighter than normal 2.5
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single OI threshold | Tiered OI response (Phase 3) | This phase | Graduated position sizing per cascade severity |
| Single basis lookback (60 bars) | Multi-window (30/60/120) with agreement | This phase | Higher quality basis signals, fewer false positives |
| Fixed ADX/ATR thresholds | Adaptive thresholds via vol percentile | This phase | Strategy self-adjusts to market regime |
| Fixed stops only | Trail metadata in signals | This phase | Execution layer can implement trailing (v2 ADV-02) |
| Correlation -> Portfolio B only | Correlation -> Portfolio A routing | This phase | High-conviction basis trades auto-execute |

## Open Questions

1. **Position sizing metadata for tiers**
   - What we know: Tier 3 should be "aggressive" (D-01). Position sizing is handled by the risk agent, not the strategy.
   - What's unclear: How to communicate tier-based sizing intent to the risk agent.
   - Recommendation: Include `suggested_size_mult` in metadata (e.g., 0.5 for Tier 1, 1.0 for Tier 2, 1.5 for Tier 3). Risk agent may ignore this, but it's available. Alternatively, higher conviction naturally leads to larger position sizing by the risk agent -- so scaling conviction with tier achieves the same effect.

2. **Funding rate array sparsity**
   - What we know: Funding rates are appended only when they change and are non-zero. The array may have very few entries.
   - What's unclear: Whether there will be enough funding rate data points for meaningful analysis.
   - Recommendation: Use the latest funding rate value (not a z-score over history) for direction confirmation. If funding_rates is empty, treat funding as neutral (does not confirm either direction). This is safe and simple.

3. **Trailing stop execution**
   - What we know: RT-02 specifies emitting trail metadata. ADV-02 (v2) handles execution-layer trailing.
   - What's unclear: Whether any downstream consumer currently reads trail metadata.
   - Recommendation: Emit the metadata anyway -- it's forward-compatible and costs nothing. The metadata dict is free-form and ignored by consumers that don't understand it.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest agents/signals/tests/ -x -q` |
| Full suite command | `python -m pytest agents/signals/tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LIQ-01 | Tier 1/2/3 classification with different stops/TPs | unit | `python -m pytest agents/signals/tests/test_liquidation_cascade.py -x -k "tier"` | Needs new tests |
| LIQ-02 | Volume surge required alongside OI drop | unit | `python -m pytest agents/signals/tests/test_liquidation_cascade.py -x -k "volume"` | Needs new tests |
| CORR-01 | Multi-window agreement (2-of-3 and 3-of-3) | unit | `python -m pytest agents/signals/tests/test_correlation.py -x -k "multi_window"` | Needs new tests |
| CORR-02 | Funding rate boosts conviction | unit | `python -m pytest agents/signals/tests/test_correlation.py -x -k "funding"` | Needs new tests |
| CORR-03 | Portfolio A routing on high conviction | unit | `python -m pytest agents/signals/tests/test_correlation.py -x -k "portfolio_a"` | Needs new tests |
| RT-01 | Adaptive ADX/ATR thresholds | unit | `python -m pytest agents/signals/tests/test_regime_trend.py -x -k "adaptive"` | Needs new tests |
| RT-02 | Trailing stop metadata emitted | unit | `python -m pytest agents/signals/tests/test_regime_trend.py -x -k "trail"` | Needs new tests |

### Sampling Rate
- **Per task commit:** `python -m pytest agents/signals/tests/ -x -q`
- **Per wave merge:** `python -m pytest agents/signals/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
None -- existing test infrastructure covers all phase requirements. Test files exist for all three strategies (`test_liquidation_cascade.py`, `test_correlation.py`, `test_regime_trend.py`). New test cases will be added to existing files.

## Sources

### Primary (HIGH confidence)
- Current strategy implementations: `agents/signals/strategies/liquidation_cascade.py`, `correlation.py`, `regime_trend.py` -- read and analyzed in full
- Phase 2 reference implementations: `agents/signals/strategies/momentum.py`, `mean_reversion.py` -- patterns verified
- FeatureStore: `agents/signals/feature_store.py` -- all data access patterns confirmed
- StandardSignal model: `libs/common/models/signal.py` -- metadata dict confirmed as free-form
- Existing test suites: `agents/signals/tests/test_*.py` -- test patterns confirmed
- YAML configs: `configs/strategies/*.yaml` -- per-instrument override structure confirmed

### Secondary (MEDIUM confidence)
- Position sizing intent via metadata/conviction -- assumption that risk agent scales position with conviction (consistent with how the system works per `agents/risk/position_sizer.py`)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies, all libraries already used
- Architecture: HIGH - all patterns have Phase 2 precedents in same codebase
- Pitfalls: HIGH - derived from reading actual code and understanding data flow
- Validation: HIGH - existing test infrastructure fully covers needs

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- internal codebase, no external dependency changes)
