# Phase 5: Cross-Cutting Quality - Research

**Researched:** 2026-03-22
**Domain:** Shared strategy utilities, session awareness, conviction normalization, per-instrument tuning
**Confidence:** HIGH

## Summary

Phase 5 extracts common patterns from Phases 2-4 into reusable shared utilities, adds session/time-of-week awareness, normalizes conviction bands across all 7 strategies, and completes deferred per-instrument tuning. The codebase already has a proven pattern for shared utilities (`agents/signals/funding_filter.py`) and per-instrument config merging (`load_strategy_config_for_instrument()`). All 7 strategies follow identical conventions for params dataclasses, config loading, and Portfolio A routing, making systematic integration straightforward.

The key challenge is sequencing: shared utilities must be built first, then integrated into all 7 strategies, then session config and conviction normalization layered on, and finally per-instrument tuning refreshed. The utilities themselves are relatively simple (function-based modules returning frozen dataclass results), but touching all 7 strategies for each integration creates a wide surface area.

**Primary recommendation:** Build all 4 shared utilities first (adaptive conviction, session classifier, conviction normalizer, swing point detection), then integrate them into strategies in a single pass per strategy, minimizing the number of times each strategy file is modified.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Significantly different parameters for weekend vs weekday crypto -- not modest adjustments. Weekend/off-hours sessions need tweaked parameters to still take trades when opportunities present despite low activity
- **D-02:** Session-aware parameters in a separate config file -- not a new layer in existing per-instrument YAML. Keeps session config cleanly separated from base strategy configs
- **D-03:** Tune parameters that affect trade frequency in quiet sessions -- min_conviction, cooldown_bars, band widths, stop multipliers, and any other params that gate signal emission
- **D-04:** All 7 strategies get session-aware params -- momentum, mean reversion, liquidation cascade, correlation, regime trend, orderbook imbalance, VWAP
- **D-05:** Post-processing step that maps raw conviction to normalized bands -- do not rewrite internal conviction models. Safer at this stage, avoids breaking tested logic
- **D-06:** Conviction bands: low (0.3-0.5), medium (0.5-0.7), high (0.7-1.0)
- **D-07:** Conviction normalization affects Portfolio A routing -- unify to a single "high band" threshold instead of per-strategy thresholds (currently momentum 0.75, mean reversion 0.65, correlation 0.70)
- **D-08:** Acceptable that raw conviction means different things across strategies -- normalization provides a consistent overlay, not a rewrite of how each strategy computes conviction
- **D-09:** Include per-instrument momentum tuning in Phase 5 -- the logic is now stable (volume confirmation, adaptive conviction, swing stops, funding boost)
- **D-10:** Follow the same research-informed approach as Phase 1 tuning -- derive values from known asset characteristics, completely separate per instrument, lower thresholds for activity
- **D-11:** Refresh per-instrument params for other strategies whose logic changed significantly in Phases 2-4 if meaningful -- correlation (multi-window + funding), regime trend (adaptive thresholds), and any others where the Phase 1 tuning no longer matches the updated logic

### Claude's Discretion
- Session classifier implementation (4 session types: crypto_weekday, crypto_weekend, equity_market_hours, equity_off_hours)
- Which specific parameters change per session and by how much (D-03)
- Session config file format and location
- How the post-processing conviction normalizer integrates into the signal pipeline
- Unified Portfolio A routing threshold value (D-07)
- Swing point detection utility API design (extracting from momentum's inline implementation)
- Adaptive conviction utility API design (extracting from inline percentileofscore usage)
- Which strategies need tuning refreshes beyond momentum (D-11)
- Specific per-instrument parameter values for momentum and refreshed strategies

### Deferred Ideas (OUT OF SCOPE)
- Volume profile strategy (VPRO-01 through VPRO-03) -- v2 requirement
- Alpha combiner improvements (ALPHA-01 through ALPHA-03) -- v2 requirement
- Multi-timeframe FeatureStore (ADV-01) -- v2 requirement
- Trailing stop state management in execution layer (ADV-02) -- v2 requirement
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| XQ-01 | Adaptive conviction thresholds -- shared utility that scales min_conviction with volatility percentile for any strategy | Extract `scipy.stats.percentileofscore` pattern used inline in momentum, mean reversion, regime trend, VWAP into `agents/signals/adaptive_conviction.py` |
| XQ-02 | Session/time-of-week classifier -- classify current time as crypto_weekday, crypto_weekend, equity_market_hours, equity_off_hours | New utility `agents/signals/session_classifier.py` returning frozen dataclass with session type and metadata |
| XQ-03 | Session-aware parameter selection -- strategies load different thresholds based on current session classification | New config file `configs/sessions.yaml` with per-strategy per-session parameter overrides; strategies merge at evaluate() time |
| XQ-04 | Cross-strategy conviction normalization -- define conviction bands and ensure consistent mapping | Post-processing utility `agents/signals/conviction_normalizer.py` applied after strategy `evaluate()` returns, before signal emission |
| XQ-05 | Dynamic stop placement utility -- swing point detection for structure-aware stops, reusable across strategies | Extract momentum's `_find_swing_low`/`_find_swing_high` into `agents/signals/swing_points.py` |
</phase_requirements>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scipy | installed | `percentileofscore` for volatility percentile computation | Already used in momentum, mean reversion, regime trend |
| numpy | installed | Array operations for swing point detection, statistical computations | Core dependency |
| PyYAML | installed | Session config loading | Already used for all strategy configs |

No new dependencies required. All utilities use existing project libraries.

## Architecture Patterns

### Recommended Utility Structure
```
agents/signals/
  adaptive_conviction.py     # XQ-01: volatility-scaled conviction thresholds
  session_classifier.py      # XQ-02: time-of-week session classification
  conviction_normalizer.py   # XQ-04: post-processing conviction band mapping
  swing_points.py            # XQ-05: swing high/low detection utility
  funding_filter.py          # (existing Phase 4 pattern to follow)
configs/
  sessions.yaml              # XQ-03: per-strategy per-session param overrides
```

### Pattern 1: Shared Utility Module (follow funding_filter.py)
**What:** Function-based utility returning a frozen dataclass result. No class state, no side effects.
**When to use:** All 4 new shared utilities.
**Example:**
```python
# Source: agents/signals/funding_filter.py (established pattern)
@dataclass(frozen=True, slots=True)
class AdaptiveConvictionResult:
    """Result of adaptive conviction threshold computation."""
    adjusted_min_conviction: float
    volatility_percentile: float
    scaling_factor: float

def compute_adaptive_min_conviction(
    atr_vals: NDArray[np.float64],
    cur_atr: float,
    base_min_conviction: float,
    low_vol_mult: float = 0.7,    # Lower threshold in quiet markets
    high_vol_mult: float = 1.2,   # Higher threshold in volatile markets
) -> AdaptiveConvictionResult:
    ...
```

### Pattern 2: Session Classifier
**What:** Classify current UTC timestamp into one of 4 session types.
**When to use:** Called by each strategy in `evaluate()` before applying session-specific params.
**Design:**
```python
from enum import Enum

class SessionType(str, Enum):
    CRYPTO_WEEKDAY = "crypto_weekday"
    CRYPTO_WEEKEND = "crypto_weekend"
    EQUITY_MARKET_HOURS = "equity_market_hours"
    EQUITY_OFF_HOURS = "equity_off_hours"

@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_type: SessionType
    is_weekend: bool
    is_equity_hours: bool
    session_progress: float  # 0-1 progress through current session

def classify_session(utc_now: datetime) -> SessionInfo:
    """Classify current time into session type.

    Logic:
    - Weekend: Saturday 00:00 UTC through Sunday 23:59 UTC
    - Equity market hours: Mon-Fri 13:30-20:00 UTC (9:30-16:00 ET)
    - Equity off hours: Mon-Fri outside 13:30-20:00 UTC
    - Crypto weekday: Mon-Fri (default when not equity hours)

    Priority: equity_market_hours > crypto_weekday > crypto_weekend
    """
```

**Session classification rules:**
- `crypto_weekend`: Saturday or Sunday (any hour)
- `equity_market_hours`: Monday-Friday, 13:30-20:00 UTC (US market hours including pre-market adjustment)
- `equity_off_hours`: Monday-Friday, outside equity hours (primarily relevant for QQQ/SPY which are less active)
- `crypto_weekday`: Monday-Friday, any hour (the default for crypto instruments during weekdays)

**Key design decision:** A timestamp can match multiple categories. For crypto instruments, use `crypto_weekday` or `crypto_weekend`. For equity perps (QQQ/SPY), additionally use `equity_market_hours` or `equity_off_hours`. The session config should support instrument-type-aware resolution.

### Pattern 3: Session Config File
**What:** Separate YAML file with per-strategy per-session parameter overrides.
**Location:** `configs/sessions.yaml` (D-02: separate from per-instrument YAML)
**Design:**
```yaml
# Per-strategy session parameter overrides.
# Keys here override the base strategy parameters when the matching
# session type is active.  Only the params that change need to be listed.
strategies:
  momentum:
    crypto_weekend:
      min_conviction: 0.30       # Lower from 0.40 default
      cooldown_bars: 3           # Faster retry
      vol_min_ratio: 0.3         # Accept lower volume
    equity_off_hours:
      min_conviction: 0.35
      cooldown_bars: 4
  mean_reversion:
    crypto_weekend:
      min_conviction: 0.30
      cooldown_bars: 6           # Lower from 10
      trend_reject_threshold: 0.7  # More permissive
    equity_off_hours:
      min_conviction: 0.35
  # ... all 7 strategies
```

### Pattern 4: Conviction Normalization (Post-Processing)
**What:** Maps raw strategy conviction to normalized bands after strategy `evaluate()` returns.
**When to use:** In `agents/signals/main.py` after getting signals from each strategy.
**Design:**
```python
@dataclass(frozen=True, slots=True)
class NormalizedConviction:
    raw_conviction: float
    normalized_conviction: float
    band: str  # "low", "medium", "high"

def normalize_conviction(raw: float) -> NormalizedConviction:
    """Map raw conviction to normalized bands.

    Bands (D-06):
      low:    0.30 - 0.50
      medium: 0.50 - 0.70
      high:   0.70 - 1.00

    Post-processing only (D-05): does not change internal conviction
    computation. Applied after evaluate() returns, before signal emission.
    """
    if raw >= 0.7:
        band = "high"
    elif raw >= 0.5:
        band = "medium"
    else:
        band = "low"
    return NormalizedConviction(
        raw_conviction=raw,
        normalized_conviction=raw,  # Identity mapping initially
        band=band,
    )
```

**Portfolio A routing unification (D-07):** Replace per-strategy `portfolio_a_min_conviction` thresholds (currently 0.75 momentum, 0.65 mean reversion, 0.70 correlation, 0.65-0.70 others) with a single threshold based on conviction band. Recommendation: route to Portfolio A when conviction is in the "high" band (>= 0.70). This is the lower bound of "high" band from D-06.

**Integration point:** The normalizer runs in `main.py` after `strategy.evaluate()` returns signals. For each signal, it classifies the conviction band and updates `suggested_target` based on the unified threshold. The signal's `conviction` value stays unchanged (D-05, D-08); only the routing decision changes.

### Pattern 5: Swing Point Extraction
**What:** Extract momentum's `_find_swing_low` and `_find_swing_high` into a shared module.
**Design:**
```python
# agents/signals/swing_points.py
def find_swing_low(
    lows: NDArray[np.float64],
    lookback: int = 20,
    order: int = 3,
) -> float | None:
    """Find the most recent swing low within the lookback window."""
    # Exact same logic as MomentumStrategy._find_swing_low

def find_swing_high(
    highs: NDArray[np.float64],
    lookback: int = 20,
    order: int = 3,
) -> float | None:
    """Find the most recent swing high within the lookback window."""
    # Exact same logic as MomentumStrategy._find_swing_high
```

**Consumers:** momentum (replace inline), mean reversion (optional structure-aware stops instead of pure ATR), regime trend (optional structure-aware stops).

### Anti-Patterns to Avoid
- **Modifying internal conviction models (D-05):** Do NOT rewrite how strategies compute conviction. The normalizer is a post-processing overlay.
- **Modifying alpha combiner:** The alpha combiner is explicitly flagged as "untouched" in STATE.md. Conviction normalization is a strategy-level utility, not an alpha combiner change.
- **Merging session config into per-instrument YAML (D-02):** Keep session config separate. The `configs/sessions.yaml` file is purpose-specific.
- **Creating utility classes with state:** Follow the function-based pattern from `funding_filter.py`. No singleton instances, no mutable state.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Volatility percentile | Custom rolling percentile | `scipy.stats.percentileofscore` | Already proven in 4 strategies, handles edge cases |
| Time zone conversions | Manual UTC offset math | `datetime` with `UTC` timezone | Python stdlib handles DST correctly |
| Config merging | Custom deep merge | Existing `load_strategy_config_for_instrument` shallow merge pattern | Proven, tested, understood |

## Common Pitfalls

### Pitfall 1: Session Config Not Loading at Strategy Init
**What goes wrong:** If session config is loaded once at strategy __init__, it cannot adapt as sessions change.
**Why it happens:** Strategies currently load params once.
**How to avoid:** Session config must be applied at evaluate()-time, not init-time. The session classifier runs on each evaluation. Session param overrides are merged dynamically.
**Warning signs:** Strategy that works during weekday hours but ignores weekend overrides.

### Pitfall 2: Conviction Normalization Breaking Existing Tests
**What goes wrong:** If the normalizer changes conviction values, existing tests checking specific conviction values will fail.
**Why it happens:** D-05 says post-processing only, but if implemented as mutation it could break.
**How to avoid:** The normalizer does NOT change the signal's conviction value. It only determines band classification and updates Portfolio A routing. Raw conviction stays as-is. Signal metadata can include `conviction_band` for observability.
**Warning signs:** Test assertions on conviction values failing.

### Pitfall 3: Weekend Session Classification Edge Cases
**What goes wrong:** UTC boundaries around Friday/Saturday and Sunday/Monday transitions.
**Why it happens:** Weekend start/end varies by market convention.
**How to avoid:** Define weekend as Saturday 00:00 UTC through Sunday 23:59:59 UTC. Friday 23:59 UTC is weekday. Monday 00:00 UTC is weekday.
**Warning signs:** Strategies behaving as weekday during early Saturday UTC.

### Pitfall 4: Per-Instrument Tuning Regression
**What goes wrong:** Updating momentum per-instrument params undoes Phase 1 tuning benefits.
**Why it happens:** Phase 2 changed momentum logic (added volume confirmation, adaptive conviction, swing stops, funding boost), so Phase 1 params may not be optimal.
**How to avoid:** Follow D-10: derive from known asset characteristics, independent per instrument, lower thresholds for activity. Don't copy Phase 1 values blindly.
**Warning signs:** Strategies producing fewer signals after tuning (opposite of goal).

### Pitfall 5: Touching Too Many Files Per Plan
**What goes wrong:** A single plan that modifies all 7 strategies + configs + utilities becomes unwieldy.
**Why it happens:** Cross-cutting changes naturally touch many files.
**How to avoid:** Build utilities first (small, focused plans), then integrate per-strategy or in small groups.

## Code Examples

### Adaptive Conviction Utility (XQ-01)
```python
# agents/signals/adaptive_conviction.py
# Source: extracted from momentum.py, mean_reversion.py, regime_trend.py inline usage
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray
from scipy.stats import percentileofscore

@dataclass(frozen=True, slots=True)
class AdaptiveConvictionResult:
    adjusted_threshold: float
    volatility_percentile: float

def compute_adaptive_threshold(
    atr_vals: NDArray[np.float64],
    cur_atr: float,
    base_threshold: float,
    low_vol_mult: float = 0.7,
    high_vol_mult: float = 1.2,
    min_samples: int = 20,
) -> AdaptiveConvictionResult:
    """Scale a threshold (e.g., min_conviction) based on volatility percentile.

    Low volatility -> lower threshold (easier to fire in quiet markets).
    High volatility -> higher threshold (stricter in noisy markets).
    """
    valid_atr = atr_vals[~np.isnan(atr_vals)]
    if len(valid_atr) < min_samples:
        return AdaptiveConvictionResult(
            adjusted_threshold=base_threshold,
            volatility_percentile=0.5,
        )
    vol_pct = float(percentileofscore(valid_atr, cur_atr)) / 100.0
    mult = low_vol_mult + (high_vol_mult - low_vol_mult) * vol_pct
    return AdaptiveConvictionResult(
        adjusted_threshold=round(base_threshold * mult, 4),
        volatility_percentile=round(vol_pct, 4),
    )
```

### Session Classifier (XQ-02)
```python
# agents/signals/session_classifier.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class SessionType(str, Enum):
    CRYPTO_WEEKDAY = "crypto_weekday"
    CRYPTO_WEEKEND = "crypto_weekend"
    EQUITY_MARKET_HOURS = "equity_market_hours"
    EQUITY_OFF_HOURS = "equity_off_hours"

@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_type: SessionType
    is_weekend: bool
    is_equity_hours: bool

def classify_session(ts: datetime) -> SessionInfo:
    """Classify a UTC timestamp into the active session type."""
    weekday = ts.weekday()  # 0=Mon, 6=Sun
    is_weekend = weekday >= 5  # Sat=5, Sun=6

    # US equity hours: 13:30-20:00 UTC (9:30 AM - 4:00 PM ET)
    hour = ts.hour
    minute = ts.minute
    time_minutes = hour * 60 + minute
    is_equity_hours = (
        not is_weekend
        and 810 <= time_minutes < 1200  # 13:30 to 20:00 in minutes
    )

    if is_weekend:
        session_type = SessionType.CRYPTO_WEEKEND
    elif is_equity_hours:
        session_type = SessionType.EQUITY_MARKET_HOURS
    else:
        session_type = SessionType.CRYPTO_WEEKDAY

    return SessionInfo(
        session_type=session_type,
        is_weekend=is_weekend,
        is_equity_hours=is_equity_hours,
    )
```

**Note on `equity_off_hours`:** This is the 4th session type from D-01. It applies to equity perps (QQQ/SPY) specifically. For crypto instruments during weekday non-equity hours, `crypto_weekday` is the correct classification. The `equity_off_hours` type should be selectable per-instrument in the session config, where QQQ/SPY use `equity_off_hours` params during weekday non-equity hours while crypto instruments use `crypto_weekday`.

### Session-Aware Parameter Loading (XQ-03)
```python
# In agents/signals/main.py or a new session config loader
def load_session_config() -> dict[str, Any]:
    """Load session-aware parameter overrides."""
    path = Path(__file__).resolve().parent.parent.parent / "configs" / "sessions.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}

def get_session_overrides(
    session_config: dict[str, Any],
    strategy_name: str,
    session_type: SessionType,
) -> dict[str, Any]:
    """Get parameter overrides for a strategy in the current session."""
    return (
        session_config
        .get("strategies", {})
        .get(strategy_name, {})
        .get(session_type.value, {})
    )
```

### Integration in main.py Signal Loop
```python
# Modified signal emission loop in agents/signals/main.py
from agents.signals.conviction_normalizer import normalize_conviction
from agents.signals.session_classifier import classify_session

# At signal emission time:
for signal in signals:
    result = normalize_conviction(signal.conviction)
    # Unified Portfolio A routing (D-07)
    if result.band == "high":
        # Override suggested_target to A if strategy supports it
        # (signal is frozen, so create new signal with updated target)
        signal = _with_portfolio_a(signal, result)
    signal = _with_conviction_metadata(signal, result)
    await publisher.publish(Channel.SIGNALS, signal_to_dict(signal))
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Inline `percentileofscore` in each strategy | Shared adaptive conviction utility | Phase 5 | Single source of truth, consistent behavior |
| No session awareness | Session classifier + per-session params | Phase 5 | Strategies adapt to market conditions |
| Per-strategy Portfolio A thresholds (0.65-0.75) | Unified threshold via conviction band | Phase 5 | Consistent routing, simpler config |
| Momentum-only swing stops | Shared swing point utility | Phase 5 | Reusable across momentum, mean reversion, regime trend |

## Strategies Needing Tuning Refresh (D-11)

Based on code review of what changed in Phases 2-4:

| Strategy | Changes Since Phase 1 Tuning | Needs Refresh? |
|----------|------------------------------|----------------|
| momentum | Volume confirmation, adaptive conviction, swing stops, funding boost (Phase 2+4) | **YES** (D-09, deferred from Phase 2) |
| mean_reversion | Multi-factor trend rejection, adaptive bands, extended targets, funding boost (Phase 2+4) | **YES** -- band width, trend threshold, extended deviation threshold are new params needing per-instrument tuning |
| correlation | Multi-window basis, funding integration (Phase 3+4) | **YES** -- 3 lookback windows and funding params are new |
| regime_trend | Adaptive ADX/ATR thresholds, trailing stop metadata (Phase 3) | **MARGINAL** -- already has extensive per-instrument overrides from Phase 1, adaptive scaling reduces need for static tuning |
| liquidation_cascade | Tiered cascade, volume surge (Phase 3) | **NO** -- tier thresholds are generic across instruments; disabled for QQQ/SPY |
| orderbook_imbalance | New strategy (Phase 4) | **NO** -- brand new, defaults are the baseline |
| vwap | New strategy (Phase 4) | **NO** -- brand new, session reset params already instrument-aware |

**Recommendation:** Refresh tuning for momentum (D-09), mean reversion, and correlation. Skip regime trend (already well-tuned with adaptive scaling), liquidation cascade (generic), orderbook imbalance (new), and VWAP (new).

## Per-Instrument Tuning Philosophy (from Phase 1, D-10)

For momentum, mean reversion, and correlation tuning refreshes:

| Instrument | Characteristics | Tuning Direction |
|------------|----------------|------------------|
| ETH-PERP | Mid-vol crypto, 24/7, liquid | Lower thresholds, moderate cooldowns |
| BTC-PERP | Lower vol, longest trends, deepest liquidity | Longer lookbacks, higher confirmation bars |
| SOL-PERP | Highest vol crypto, thinner book, faster moves | Wider stops, shorter lookbacks, accept more volatility |
| QQQ-PERP | Equity derivative, session-bound activity | Longer cooldowns, equity-hours focus |
| SPY-PERP | Most liquid equity, steady trends | Conservative thresholds, longest cooldowns |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | `pyproject.toml` |
| Quick run command | `python -m pytest agents/signals/tests/ -x -q` |
| Full suite command | `python -m pytest agents/signals/tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| XQ-01 | Adaptive conviction scales threshold with vol percentile | unit | `python -m pytest agents/signals/tests/test_adaptive_conviction.py -x` | Wave 0 |
| XQ-02 | Session classifier returns correct type for each time | unit | `python -m pytest agents/signals/tests/test_session_classifier.py -x` | Wave 0 |
| XQ-03 | Session overrides applied to strategy params | unit | `python -m pytest agents/signals/tests/test_session_params.py -x` | Wave 0 |
| XQ-04 | Conviction normalization maps to correct bands, unified routing | unit | `python -m pytest agents/signals/tests/test_conviction_normalizer.py -x` | Wave 0 |
| XQ-05 | Swing point detection matches momentum's inline behavior | unit | `python -m pytest agents/signals/tests/test_swing_points.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest agents/signals/tests/ -x -q`
- **Per wave merge:** `python -m pytest agents/signals/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/signals/tests/test_adaptive_conviction.py` -- covers XQ-01
- [ ] `agents/signals/tests/test_session_classifier.py` -- covers XQ-02
- [ ] `agents/signals/tests/test_session_params.py` -- covers XQ-03
- [ ] `agents/signals/tests/test_conviction_normalizer.py` -- covers XQ-04
- [ ] `agents/signals/tests/test_swing_points.py` -- covers XQ-05

## Open Questions

1. **Equity off-hours vs crypto weekday resolution for equity perps**
   - What we know: QQQ/SPY should use `equity_off_hours` during non-market hours; crypto instruments should use `crypto_weekday`
   - What's unclear: Whether the session config should have instrument-type keys or if strategies handle this internally
   - Recommendation: Session config includes an `instrument_types` mapping (e.g., `crypto: [ETH-PERP, BTC-PERP, SOL-PERP]`, `equity: [QQQ-PERP, SPY-PERP]`). When an equity instrument is outside market hours, it uses `equity_off_hours` params; when a crypto instrument is on a weekday, it uses `crypto_weekday`. This keeps the session classifier instrument-agnostic while letting config resolution handle instrument-type awareness.

2. **Unified Portfolio A threshold value**
   - What we know: Current thresholds are 0.75 (momentum), 0.65 (mean reversion), 0.70 (correlation, regime trend), 0.65 (OBI)
   - What's unclear: Whether 0.70 (high band floor from D-06) is the right unified value
   - Recommendation: Use 0.70 as the unified threshold (matches "high" band floor). This tightens mean reversion and OBI (from 0.65) and loosens momentum (from 0.75). Acceptable tradeoff since conviction normalization provides consistent meaning.

3. **How to handle frozen StandardSignal with updated target**
   - What we know: StandardSignal is `frozen=True`, so conviction normalization cannot mutate it
   - What's unclear: Whether to create a new signal or use a wrapper
   - Recommendation: Use `dataclasses.replace()` to create a new signal with updated `suggested_target` and enriched `metadata`. This is idiomatic for frozen dataclasses.

## Sources

### Primary (HIGH confidence)
- `agents/signals/strategies/momentum.py` -- inline swing points, percentileofscore usage, Portfolio A at 0.75
- `agents/signals/strategies/mean_reversion.py` -- inline adaptive bands, percentileofscore, Portfolio A at 0.65
- `agents/signals/strategies/regime_trend.py` -- inline adaptive thresholds, percentileofscore, Portfolio A at 0.70
- `agents/signals/strategies/vwap.py` -- session reset mechanism, session progress computation
- `agents/signals/strategies/correlation.py` -- funding integration, Portfolio A at 0.70
- `agents/signals/strategies/orderbook_imbalance.py` -- 3-component conviction, Portfolio A at 0.65
- `agents/signals/strategies/liquidation_cascade.py` -- tiered cascade, volume surge
- `agents/signals/funding_filter.py` -- established shared utility pattern
- `agents/signals/main.py` -- strategy registration, signal emission loop, config loading
- `libs/common/config.py` -- config loading, per-instrument merge, validation
- `configs/strategies/regime_trend.yaml` -- reference per-instrument override format

### Secondary (MEDIUM confidence)
- Phase 1-4 decisions in STATE.md -- accumulated context on design choices
- CONTEXT.md -- user decisions constraining Phase 5 scope

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all existing libraries
- Architecture: HIGH -- follows established patterns from funding_filter.py and config.py
- Pitfalls: HIGH -- derived from direct code analysis of all 7 strategies
- Tuning: MEDIUM -- specific parameter values for momentum/MR/correlation tuning require asset-specific reasoning at implementation time

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- no external dependency changes)
