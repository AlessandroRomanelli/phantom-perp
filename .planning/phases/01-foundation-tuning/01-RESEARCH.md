# Phase 1: Foundation and Per-Instrument Tuning - Research

**Researched:** 2026-03-21
**Domain:** Config validation, per-instrument YAML tuning, FeatureStore extensions, dependency management
**Confidence:** HIGH

## Summary

Phase 1 is entirely infrastructure and configuration work -- no strategy logic changes. The codebase already has most of the scaffolding: per-instrument strategy instantiation exists in `agents/signals/main.py`, the `load_strategy_config_for_instrument()` merge function works, and `regime_trend.yaml` provides a complete reference pattern for per-instrument overrides. The main work is: (1) adding config schema validation with error-on-unknown at base level and warn-on-unknown at instrument level, (2) refactoring cooldown from a single `_bars_since_signal` integer to a per-instrument dict (since each strategy instance is already per-instrument, this is actually a misconception -- see analysis below), (3) extending FeatureStore with `timestamps` and `bar_volume` properties, (4) adding `VWAP` and `VOLUME_PROFILE` to `SignalSource`, (5) adding `scipy` and `bottleneck` to `pyproject.toml`, and (6) creating per-instrument parameter overrides for all 5 strategy configs across all 5 instruments.

**Critical finding on INFRA-01 (cooldown):** Each instrument already gets its own strategy instances via `build_strategies_for_instrument()` in `main.py` line 89. Each strategy instance has its own `_bars_since_signal` counter. The cooldown is already per-instrument because the instances are per-instrument. INFRA-01 may already be satisfied. The planner should verify this by checking whether any shared state leaks between instruments, but the architecture appears correct.

**Primary recommendation:** Start with infra changes (enum additions, FeatureStore, dependencies, config validation), then layer per-instrument YAML tuning on top, using `regime_trend.yaml` as the template pattern.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Derive parameter values from known asset characteristics -- ETH volatility profile, BTC liquidity depth, SOL high-vol/thin-book, QQQ/SPY equity dynamics
- **D-02:** Treat each instrument independently -- no "reference" instrument with relative scaling; each gets its own research-informed parameter set
- **D-03:** Completely separate parameter sets for crypto vs equity perps -- not mostly-shared with overrides
- **D-04:** Actively lower thresholds to increase signal frequency so performance can be evaluated -- the current paper trading showed too little activity, especially on weekends
- **D-05:** Error and halt on unknown YAML keys at the base level -- typos must not silently use defaults
- **D-06:** Validate the entire YAML structure (top-level keys like `enabled`, `instruments`, `parameters`), not just the parameters block
- **D-07:** Per-instrument override keys also validated, but warn instead of halt -- less strict than base level
- **D-08:** Only flag unknown keys -- no special handling for deprecated/removed parameters
- **D-09:** Momentum stays globally disabled (`enabled: false`) until Phase 2 improves it
- **D-10:** Create a formalized "strategy matrix" -- explicit declaration of which strategies run on which instruments, rather than each strategy YAML independently deciding
- **D-11:** QQQ/SPY enablement for liquidation cascade and correlation strategies left to Claude's discretion during planning based on whether OI/basis dynamics apply to equity perps
- **D-12:** No session logic in Phase 1 -- avoid any market-hours gating or session classification until Phase 5 delivers it properly
- **D-13:** QQQ/SPY perps are active on weekends too -- configs should not assume market-hours-only operation
- **D-14:** Single parameter profile per instrument -- no weekday/weekend split until Phase 5's session classifier
- **D-15:** Let natural low volume/volatility suppress signals organically outside active hours rather than adding explicit gates

### Claude's Discretion
- Whether liquidation cascade should be enabled for QQQ-PERP and SPY-PERP (D-11)
- Whether correlation strategy should be disabled for equity perps
- Strategy matrix format and location (new file vs embedded in existing config)
- Exact parameter values per instrument (research-informed, but specific numbers are implementation detail)
- How config schema validation is implemented (Pydantic model vs manual checks)

### Deferred Ideas (OUT OF SCOPE)
- Session-aware parameter profiles (weekday/weekend, market hours/off hours) -- Phase 5 (XQ-02, XQ-03)
- Adaptive conviction thresholds that scale with volatility -- Phase 5 (XQ-01)
- Volume profile strategy using bar_volume data -- Phase 4 (deferred to v2)

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INFRA-01 | Per-instrument cooldown tracking | Already per-instrument via separate strategy instances; verify no shared state leaks. See "Critical finding" in summary. |
| INFRA-02 | Config schema validation -- warn on unknown YAML keys | Implement via Pydantic model or manual key-set diff against Params dataclass fields. D-05/D-06/D-07 constrain behavior. |
| INFRA-03 | Config diff logging at startup | Add logging in `build_strategies_for_instrument()` comparing merged params to defaults. |
| INFRA-04 | Add scipy and bottleneck dependencies | scipy already installed (1.17.1); bottleneck needs install (1.6.0). Add both to pyproject.toml. |
| INFRA-05 | Add VWAP and VOLUME_PROFILE to SignalSource enum | Two-line addition to `libs/common/models/enums.py`. Inert until Phase 4. |
| INFRA-06 | Add timestamps property to FeatureStore | `_timestamps` deque already exists; just add public property returning NDArray. |
| INFRA-07 | Compute bar_volume deltas in FeatureStore | Compute `np.diff()` on volumes array, or store incremental deltas in a new deque. |
| TUNE-01 | ETH-PERP strategy configs | Per-instrument overrides in all 4 active strategy YAMLs for ETH characteristics. |
| TUNE-02 | BTC-PERP strategy configs | Per-instrument overrides for BTC (already partial in regime_trend.yaml). |
| TUNE-03 | SOL-PERP strategy configs | Per-instrument overrides for SOL high-vol characteristics. |
| TUNE-04 | QQQ-PERP strategy configs | Per-instrument overrides for equity perp with 24/7 operation (no session gating). |
| TUNE-05 | SPY-PERP strategy configs | Per-instrument overrides for equity perp with 24/7 operation (no session gating). |

</phase_requirements>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | 2.6+ | Config validation models | Already used for settings; natural fit for YAML schema validation |
| pyyaml | 6+ | YAML parsing | Already the config loader |
| numpy | 1.26+ | NDArray for FeatureStore properties | Already used throughout |
| structlog | 24.1+ | Config diff logging | Already the logging framework |

### New Dependencies (INFRA-04)
| Library | Version | Purpose | Why Needed |
|---------|---------|---------|------------|
| scipy | >=1.14,<2 | Statistical computations (z-scores, distributions) | Needed by Phase 3-4 strategies; already installed in venv (1.17.1) |
| bottleneck | >=1.4,<2 | Fast rolling window operations (move_mean, move_std) | Faster than pure numpy for rolling computations; not yet installed |

**Installation (add to pyproject.toml dependencies):**
```toml
"scipy>=1.14,<2",
"bottleneck>=1.4,<2",
```

**Version verification:**
- scipy: 1.17.1 current on PyPI (verified 2026-03-21), already in venv
- bottleneck: 1.6.0 current on PyPI (verified 2026-03-21), not yet installed

**Docker note:** scipy requires BLAS/LAPACK. The existing Docker image already has these (scipy is a transitive dependency of scikit-learn which is already in the project). Bottleneck is a C extension but pip provides prebuilt wheels for linux/amd64.

## Architecture Patterns

### Config Validation Pattern (INFRA-02, D-05 through D-08)

**Recommended approach: Manual key-set diff against Params dataclass fields.**

Pydantic models would require duplicating every strategy's params dataclass as a Pydantic model. Instead, use the simpler approach: extract the set of valid field names from the strategy's `@dataclass` params class, diff against YAML keys, and error/warn accordingly.

```python
# In config.py -- new validation function
import dataclasses

def validate_strategy_config(
    strategy_name: str,
    config: dict[str, Any],
    params_cls: type,  # The strategy's Params dataclass
) -> None:
    """Validate strategy YAML config keys against known schema.

    Raises ValueError for unknown base-level keys (D-05, D-06).
    Logs warning for unknown instrument-level keys (D-07).
    """
    # Known top-level keys in strategy YAML
    VALID_TOP_KEYS = {"strategy", "parameters", "instruments"}
    VALID_STRATEGY_KEYS = {"name", "enabled", "weight"}

    # Check top-level keys (D-06: error and halt)
    unknown_top = set(config.keys()) - VALID_TOP_KEYS - {"_instrument"}
    if unknown_top:
        raise ValueError(
            f"Strategy '{strategy_name}': unknown top-level keys: {unknown_top}"
        )

    # Check strategy block keys
    strategy_block = config.get("strategy", {})
    unknown_strategy = set(strategy_block.keys()) - VALID_STRATEGY_KEYS
    if unknown_strategy:
        raise ValueError(
            f"Strategy '{strategy_name}': unknown strategy keys: {unknown_strategy}"
        )

    # Check parameter keys against dataclass fields (D-05: error and halt)
    valid_params = {f.name for f in dataclasses.fields(params_cls)}
    yaml_params = set(config.get("parameters", {}).keys())
    unknown_params = yaml_params - valid_params
    if unknown_params:
        raise ValueError(
            f"Strategy '{strategy_name}': unknown parameter keys: {unknown_params}"
        )
```

**For instrument-level validation (D-07: warn, don't halt):**
```python
# In the instrument override section
for instrument_id, overrides in config.get("instruments", {}).items():
    if "parameters" in overrides:
        unknown = set(overrides["parameters"].keys()) - valid_params
        if unknown:
            logger.warning(
                "unknown_instrument_params",
                strategy=strategy_name,
                instrument=instrument_id,
                unknown_keys=list(unknown),
            )
```

### Config Diff Logging Pattern (INFRA-03)

Log at strategy instantiation time, comparing merged params to base defaults:

```python
def log_config_diff(
    strategy_name: str,
    instrument_id: str,
    merged_params: dict[str, Any],
    default_params: dict[str, Any],
) -> None:
    """Log which parameters differ from defaults for this instrument."""
    diffs: dict[str, dict[str, Any]] = {}
    for key, merged_val in merged_params.items():
        default_val = default_params.get(key)
        if merged_val != default_val:
            diffs[key] = {"default": default_val, "override": merged_val}

    if diffs:
        logger.info(
            "instrument_config_overrides",
            strategy=strategy_name,
            instrument=instrument_id,
            overrides=diffs,
        )
    else:
        logger.info(
            "instrument_using_defaults",
            strategy=strategy_name,
            instrument=instrument_id,
        )
```

### FeatureStore Extension Pattern (INFRA-06, INFRA-07)

The `_timestamps` deque already exists and is populated. Adding the property is trivial.

For bar_volume (INFRA-07): The `_volumes` deque stores `volume_24h` which is a rolling 24h total, not per-bar volume. The delta between consecutive samples approximates per-bar volume:

```python
@property
def timestamps(self) -> NDArray[np.float64]:
    """Sample timestamps as numpy array of Unix epoch floats."""
    return np.array(
        [t.timestamp() for t in self._timestamps], dtype=np.float64
    )

@property
def bar_volumes(self) -> NDArray[np.float64]:
    """Per-bar volume deltas between consecutive 24h volume samples.

    Returns array of length (sample_count - 1). Each element is the
    change in 24h rolling volume between consecutive samples.
    """
    if len(self._volumes) < 2:
        return np.array([], dtype=np.float64)
    vols = np.array(self._volumes, dtype=np.float64)
    return np.diff(vols)
```

**Alternative for timestamps:** Return `datetime` objects directly instead of epoch floats, since VWAP session reset logic in Phase 4 will need datetime comparison. Consider returning `list[datetime]` or keeping the deque-based accessor. The epoch-float approach is more consistent with other properties returning NDArray.

### Strategy Matrix Pattern (D-10)

**Recommendation:** New file `configs/strategy_matrix.yaml` rather than embedding in `default.yaml` or individual strategy YAMLs. Rationale: single source of truth for "what runs where", easy to audit, separates concern from per-strategy parameter tuning.

```yaml
# configs/strategy_matrix.yaml
# Declares which strategies are enabled for which instruments.
# Individual strategy YAMLs can still set `enabled: false` globally,
# but this matrix controls per-instrument enablement.

strategies:
  momentum:
    enabled: false  # Globally disabled until Phase 2 (D-09)
    instruments: []

  mean_reversion:
    enabled: true
    instruments:
      - ETH-PERP
      - BTC-PERP
      - SOL-PERP
      - QQQ-PERP
      - SPY-PERP

  liquidation_cascade:
    enabled: true
    instruments:
      - ETH-PERP
      - BTC-PERP
      - SOL-PERP
      # QQQ/SPY: see D-11 discretion note below

  correlation:
    enabled: true
    instruments:
      - ETH-PERP
      - BTC-PERP
      - SOL-PERP
      # QQQ/SPY: see discretion note below

  regime_trend:
    enabled: true
    instruments:
      - ETH-PERP
      - BTC-PERP
      - SOL-PERP
      - QQQ-PERP
      - SPY-PERP
```

**Integration:** `build_strategies_for_instrument()` in `agents/signals/main.py` reads the matrix to determine enablement, falling back to per-strategy YAML `enabled` field.

### Discretion Decisions (D-11)

**Liquidation cascade for QQQ/SPY:** DISABLE. Liquidation cascade relies on OI drops indicating forced liquidation events. Equity perps on Coinbase INTX have much lower OI and fewer leveraged participants than crypto. Forced liquidation cascades are a crypto-native phenomenon. Enabling this for equity perps would likely produce false signals from normal OI fluctuations.

**Correlation strategy for QQQ/SPY:** ENABLE with caution. Basis divergence (mark vs index) and OI/price divergence are valid concepts for equity perps. The SPY/QQQ index prices track real equity indices, so mark-index basis can indicate perp mispricing. Keep the higher conviction thresholds for equity.

### Per-Instrument YAML Structure

Follow `regime_trend.yaml` as the reference pattern. Each strategy config gets an `instruments:` section with per-instrument overrides.

```yaml
# Pattern for all strategy YAMLs
strategy:
  name: "<name>"
  enabled: true
  weight: <float>

parameters:
  # Base defaults
  param_a: <value>
  param_b: <value>

instruments:
  ETH-PERP:
    parameters:
      param_a: <eth_value>
  BTC-PERP:
    parameters:
      param_a: <btc_value>
  # ... etc
```

### Anti-Patterns to Avoid
- **Shared cooldown state across instruments:** Each strategy instance is already per-instrument. Do NOT introduce a shared dict of cooldowns -- the per-instance `_bars_since_signal` is the correct pattern.
- **Float parameters for monetary amounts:** Strategy parameters like thresholds are `float` (not `Decimal`) because they are ratios/percentages, not monetary amounts. This is correct and should not change.
- **Hardcoded instrument lists:** Use `ACTIVE_INSTRUMENT_IDS` from constants, not hardcoded lists in strategy code.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML schema validation | Custom recursive validator | Dataclass field introspection + set diff | Params dataclasses already define the schema; just compare key sets |
| Rolling window stats | Manual numpy loops | `bottleneck.move_mean`, `bottleneck.move_std` | 10-100x faster than numpy for moving window ops, handles NaN correctly |
| Per-bar volume from 24h rolling | Complex time-windowed aggregation | `np.diff()` on volume_24h series | Simple delta approximation is sufficient per CONTEXT.md |
| Config merging | Deep recursive merge | Existing `load_strategy_config_for_instrument()` shallow merge | Already works; shallow merge is by design (override individual keys) |

## Common Pitfalls

### Pitfall 1: Cooldown Already Per-Instrument
**What goes wrong:** Implementing a dict-based cooldown when it is not needed.
**Why it happens:** INFRA-01 says "cooldown state must be keyed by instrument ID, not shared globally." But each strategy instance is already per-instrument (see `build_strategies_for_instrument()` in main.py line 89).
**How to avoid:** Verify by reading the instantiation code. Each instrument gets its own `MomentumStrategy`, `MeanReversionStrategy`, etc. Each instance has its own `_bars_since_signal`. The requirement may already be met.
**Warning signs:** If you find yourself adding instrument_id keys to a cooldown dict inside a strategy, you are likely solving a non-problem.

### Pitfall 2: volume_24h is Cumulative, Not Per-Bar
**What goes wrong:** Treating `volume_24h` from MarketSnapshot as per-bar volume.
**Why it happens:** The name suggests "24-hour volume" which is a rolling cumulative figure from the exchange.
**How to avoid:** INFRA-07 specifically asks for deltas. Use `np.diff()` on the stored volume_24h series to get approximate per-bar volume changes. Note: deltas can be negative when old volume rolls off the 24h window.
**Warning signs:** Negative bar_volume values are normal and expected -- they indicate the 24h rolling window dropped a high-volume period.

### Pitfall 3: Config Validation Must Happen at Load Time
**What goes wrong:** Validating config after strategy instantiation, missing the "halt on unknown keys" requirement.
**Why it happens:** Natural to validate inside strategy `__init__`, but D-05 says "error and halt" which means the process should fail to start.
**How to avoid:** Validate in `load_strategy_config()` or `build_strategies_for_instrument()` before constructing strategy instances. Raise `ValueError` that propagates to agent startup.
**Warning signs:** Unknown keys being logged but not preventing startup at base level.

### Pitfall 4: ETH Default Confusion
**What goes wrong:** Not creating ETH-PERP overrides because "defaults are already tuned for ETH."
**Why it happens:** The system was originally ETH-only, so defaults may be ETH-appropriate.
**How to avoid:** Per D-02, treat each instrument independently. Even if defaults happen to work for ETH, create explicit ETH-PERP overrides in the YAML so the config is self-documenting and auditable.
**Warning signs:** ETH-PERP section missing from instrument overrides while all others have entries.

### Pitfall 5: Momentum Config Changes
**What goes wrong:** Adding per-instrument overrides to momentum.yaml despite it being disabled.
**Why it happens:** TUNE-01 through TUNE-05 say "all strategies."
**How to avoid:** Per D-09, momentum stays disabled. Do NOT add per-instrument overrides to momentum.yaml. Leave it untouched until Phase 2.
**Warning signs:** momentum.yaml having an `instruments:` section.

## Code Examples

### SignalSource Enum Addition (INFRA-05)
```python
# libs/common/models/enums.py -- add to SignalSource
class SignalSource(str, Enum):
    # ... existing entries ...
    REGIME_TREND = "regime_trend"
    VWAP = "vwap"                    # New
    VOLUME_PROFILE = "volume_profile"  # New
```

### FeatureStore timestamps Property (INFRA-06)
```python
# agents/signals/feature_store.py -- add property
@property
def timestamps(self) -> NDArray[np.float64]:
    """Sample timestamps as Unix epoch seconds."""
    return np.array(
        [t.timestamp() for t in self._timestamps], dtype=np.float64
    )
```

Note: `_timestamps` deque already exists and is already populated in the `update()` method (line 92). This is purely adding a public accessor.

### FeatureStore bar_volumes Property (INFRA-07)
```python
@property
def bar_volumes(self) -> NDArray[np.float64]:
    """Per-bar volume deltas from consecutive 24h volume samples.

    Length is (sample_count - 1). Values can be negative when
    high-volume periods roll off the 24h window.
    """
    if len(self._volumes) < 2:
        return np.array([], dtype=np.float64)
    return np.diff(np.array(self._volumes, dtype=np.float64))
```

### Config Validation Integration Point
```python
# In load_strategy_config() or a new validate_strategy_config()
# Called before strategy instantiation in build_strategies_for_instrument()

from agents.signals.strategies.momentum import MomentumParams
from agents.signals.strategies.mean_reversion import MeanReversionParams
# ... etc

STRATEGY_PARAMS_CLASSES: dict[str, type] = {
    "momentum": MomentumParams,
    "mean_reversion": MeanReversionParams,
    "liquidation_cascade": LiquidationCascadeParams,
    "correlation": CorrelationParams,
    "regime_trend": RegimeTrendParams,
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Universal defaults for all instruments | Per-instrument YAML overrides | This phase | Each asset trades with appropriate thresholds |
| Silent ignore on bad YAML keys | Validate + error/warn on unknown keys | This phase | Typos caught at startup instead of running with wrong params |
| No bar-level volume data | bar_volume deltas from 24h rolling | This phase | Foundation for VWAP and volume profile strategies in Phase 4 |

## Open Questions

1. **INFRA-01 verification**
   - What we know: Strategy instances are created per-instrument in `build_strategies_for_instrument()`. Each instance has its own `_bars_since_signal`.
   - What's unclear: Whether the requirement author was aware of this architecture. There may be an edge case we are not seeing.
   - Recommendation: Add a test that explicitly verifies an ETH signal does not suppress a BTC signal. If the test passes with current code, INFRA-01 is already satisfied and only needs the test as documentation.

2. **Strategy matrix integration point**
   - What we know: D-10 wants a formalized matrix. Currently enablement is scattered across individual YAML files.
   - What's unclear: Whether the matrix should be authoritative (overrides per-strategy YAML) or advisory (validated against per-strategy YAML).
   - Recommendation: Make the matrix authoritative for per-instrument enablement. The per-strategy YAML `enabled` field becomes the global toggle; the matrix controls per-instrument.

3. **Negative bar_volume deltas**
   - What we know: `np.diff()` on 24h rolling volume can produce negatives.
   - What's unclear: Whether downstream strategies (Phase 4) can handle negative deltas.
   - Recommendation: Document the behavior. Do not clamp to zero -- negative deltas carry information (declining volume).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio 0.23+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `/Users/aroman/Work/phantom-perp/.venv/bin/pytest agents/signals/tests/ -x -q` |
| Full suite command | `/Users/aroman/Work/phantom-perp/.venv/bin/pytest agents/signals/tests/ libs/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFRA-01 | Per-instrument cooldown isolation | unit | `.venv/bin/pytest agents/signals/tests/test_main.py -x -k cooldown` | Needs new test |
| INFRA-02 | Unknown YAML keys error/warn | unit | `.venv/bin/pytest agents/signals/tests/test_config_validation.py -x` | Wave 0 |
| INFRA-03 | Config diff logged at startup | unit | `.venv/bin/pytest agents/signals/tests/test_config_validation.py -x -k diff` | Wave 0 |
| INFRA-04 | scipy/bottleneck importable | smoke | `.venv/bin/pytest agents/signals/tests/test_dependencies.py -x` | Wave 0 |
| INFRA-05 | VWAP/VOLUME_PROFILE in SignalSource | unit | `.venv/bin/pytest agents/signals/tests/test_main.py -x -k source` | Inline check |
| INFRA-06 | FeatureStore.timestamps accessor | unit | `.venv/bin/pytest agents/signals/tests/test_feature_store.py -x -k timestamps` | Needs new test |
| INFRA-07 | FeatureStore.bar_volumes deltas | unit | `.venv/bin/pytest agents/signals/tests/test_feature_store.py -x -k bar_volume` | Needs new test |
| TUNE-01-05 | Per-instrument configs load correctly | unit | `.venv/bin/pytest agents/signals/tests/test_config_validation.py -x -k instrument` | Wave 0 |

### Sampling Rate
- **Per task commit:** `.venv/bin/pytest agents/signals/tests/ -x -q`
- **Per wave merge:** `.venv/bin/pytest agents/signals/tests/ libs/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/signals/tests/test_config_validation.py` -- covers INFRA-02, INFRA-03, TUNE-01-05 config loading
- [ ] `agents/signals/tests/test_feature_store.py` -- needs new tests for `timestamps` and `bar_volumes` (file exists, add tests)
- [ ] `agents/signals/tests/test_main.py` -- needs test for per-instrument cooldown isolation (INFRA-01)
- [ ] No new framework install needed -- pytest already configured

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection of all canonical reference files listed in CONTEXT.md
- `libs/common/config.py` -- config loading and merge logic verified
- `agents/signals/main.py` -- per-instrument instantiation confirmed (lines 89-115)
- `agents/signals/feature_store.py` -- `_timestamps` deque exists (line 44), `_volumes` stores volume_24h (line 94)
- `agents/signals/strategies/*.py` -- all 5 strategies use identical `_bars_since_signal` cooldown pattern
- `configs/strategies/regime_trend.yaml` -- reference pattern for per-instrument overrides
- PyPI version check: scipy 1.17.1, bottleneck 1.6.0 (verified 2026-03-21)

### Secondary (MEDIUM confidence)
- Asset characteristic assumptions (ETH volatility, BTC liquidity, SOL thin book) -- based on general market knowledge, appropriate for initial tuning per D-04 (optimize for activity)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries verified in pyproject.toml and PyPI
- Architecture: HIGH -- all patterns derived from direct codebase inspection
- Pitfalls: HIGH -- identified from actual code analysis, not hypothetical
- Per-instrument tuning values: MEDIUM -- exact parameter values will need empirical validation in paper trading

**Research date:** 2026-03-21
**Valid until:** 2026-04-21 (stable infrastructure work, no fast-moving dependencies)
