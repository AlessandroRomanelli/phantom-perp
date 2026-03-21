# Architecture Patterns

**Domain:** Perpetual futures trading strategy enhancement
**Researched:** 2026-03-21

## Recommended Architecture

The existing architecture is well-suited for strategy additions with zero infrastructure changes. New strategies (funding arb, orderbook imbalance, VWAP, volume profile) and per-instrument tuning plug directly into the current `SignalStrategy` base class pattern. The main architectural work is extending FeatureStore with two new derived series and following the established per-instrument config convention for all strategies.

### System Context: Where New Work Lives

```
MarketSnapshot (from ingestion)
        |
        v
  FeatureStore (per-instrument rolling buffer)
        |
        v
  SignalStrategy.evaluate(snapshot, store)
        |
        v
  StandardSignal (published to Redis stream:signals)
        |
        v
  Alpha Combiner (untouched — consumes signals)
```

All changes are confined to the Signal Generation Layer:
- `agents/signals/strategies/` -- new strategy files
- `agents/signals/feature_store.py` -- minor extensions
- `agents/signals/main.py` -- register new strategy classes
- `configs/strategies/` -- new YAML config files with per-instrument overrides

Nothing downstream (alpha, risk, execution, reconciliation) changes.

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `FeatureStore` | Rolling buffer of price, volume, OI, funding, orderbook data; computes derived series (VWAP, volume profile bins) | Consumed by all strategies via `store` parameter |
| `SignalStrategy` subclass | Reads snapshot + store, emits `StandardSignal` list | Receives snapshot/store from orchestrator; emits signals |
| `main.py` orchestrator | Per-instrument strategy instantiation, config loading, parallel evaluation | Reads Redis snapshots, writes Redis signals |
| Strategy YAML config | Per-strategy defaults + per-instrument overrides | Read at agent startup by `load_strategy_config_for_instrument()` |
| `SignalSource` enum | Identifies signal origin for downstream weighting/routing | Used in `StandardSignal.source` field |

## How New Strategies Fit the Existing Pattern

### The Contract

Every strategy must:
1. Subclass `SignalStrategy` (from `agents/signals/strategies/base.py`)
2. Accept `config: dict[str, Any]` in `__init__()` and parse `config["parameters"]` into a typed params dataclass
3. Implement `name` (property), `enabled` (property), `evaluate(snapshot, store) -> list[StandardSignal]`
4. Override `min_history` if the strategy needs indicator warm-up samples
5. Use an existing `SignalSource` enum value (slots already exist for `FUNDING_ARB` and `ORDERBOOK_IMBALANCE`)

### Registration: One Line in main.py

Adding a strategy requires exactly one change to `agents/signals/main.py`:

```python
# In STRATEGY_CLASSES dict:
STRATEGY_CLASSES: dict[str, type[SignalStrategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "liquidation_cascade": LiquidationCascadeStrategy,
    "correlation": CorrelationStrategy,
    "regime_trend": RegimeTrendStrategy,
    # New strategies:
    "funding_arb": FundingArbStrategy,
    "orderbook_imbalance": OrderbookImbalanceStrategy,
    "vwap": VWAPStrategy,
    "volume_profile": VolumeProfileStrategy,
}
```

The orchestrator already handles per-instrument instantiation via `build_strategies_for_instrument()`, config loading via `load_strategy_config_for_instrument()`, parallel evaluation, error isolation (per-strategy try/except), and dedup/publishing.

### Constructor Pattern (Follow Existing Convention)

Every strategy follows this pattern (exemplified by MomentumStrategy):

```python
@dataclass
class FundingArbParams:
    zscore_threshold: float = 2.0
    min_annualized_rate_pct: float = 10.0
    # ... all params with defaults

class FundingArbStrategy(SignalStrategy):
    def __init__(self, params=None, config=None):
        self._params = params or FundingArbParams()
        if config:
            p = config.get("parameters", {})
            self._params = FundingArbParams(
                zscore_threshold=p.get("zscore_threshold", self._params.zscore_threshold),
                # ... merge from config
            )
        self._enabled = True
        self._instrument = config.get("_instrument", "") if config else ""
```

The `config` dict arrives with per-instrument overrides already merged (shallow merge on `parameters` key). The strategy never needs to know about the instrument override mechanism.

## Per-Instrument Config Loading Pattern

### Already Solved

The config system (`libs/common/config.py :: load_strategy_config_for_instrument()`) already implements:

1. Load base YAML from `configs/strategies/<strategy>.yaml`
2. Look up `instruments.<instrument_id>` section
3. Shallow-merge `parameters` (instrument keys override, base keys kept)
4. Support `enabled: false` per instrument
5. Support `weight` override per instrument
6. Inject `_instrument` key into result dict

### What Per-Instrument Configs Need

For new strategies, create YAML files following this established pattern:

```yaml
# configs/strategies/vwap.yaml
strategy:
  name: "vwap"
  enabled: true
  weight: 0.15

parameters:
  deviation_threshold_pct: 0.5
  session_reset_hours: 24
  min_conviction: 0.5
  # ...

instruments:
  BTC-PERP:
    parameters:
      deviation_threshold_pct: 0.7  # BTC needs wider threshold
  SOL-PERP:
    parameters:
      deviation_threshold_pct: 0.3  # SOL mean-reverts tighter
  QQQ-PERP:
    parameters:
      session_reset_hours: 6.5      # Equity session length
  SPY-PERP:
    parameters:
      session_reset_hours: 6.5
```

For existing strategies getting per-instrument tuning, the pattern is already demonstrated in `regime_trend.yaml` (BTC, SOL, QQQ, SPY all have overrides). The same structure should be added to `momentum.yaml`, `mean_reversion.yaml`, `correlation.yaml`, and `liquidation_cascade.yaml`.

### SignalSource Enum: Two New Values Needed

Current enum has slots for `FUNDING_ARB` and `ORDERBOOK_IMBALANCE`. VWAP and volume profile need new enum values:

```python
class SignalSource(str, Enum):
    # ... existing ...
    VWAP = "vwap"
    VOLUME_PROFILE = "volume_profile"
```

This is the only shared infrastructure change required. It is backward-compatible (new enum values, no existing values change). The alpha combiner and portfolio router already handle unknown sources via default routing rules.

## FeatureStore Extensions Needed

### Current State

The FeatureStore already tracks: closes, highs, lows, timestamps, index_prices, volumes, open_interests, orderbook_imbalances, funding_rates. This is sufficient for funding arb and orderbook imbalance strategies.

### Extensions for VWAP Strategy

VWAP requires cumulative volume-weighted price over a session. Two approaches:

**Option A: Compute in-strategy (recommended).** The strategy computes VWAP from `store.closes` and `store.volumes` arrays directly. No FeatureStore changes. VWAP is just `cumsum(price * volume) / cumsum(volume)` over the session window. The strategy tracks session boundaries internally.

**Why Option A:** VWAP is a single-strategy concern. Adding it to FeatureStore would couple the store to a specific strategy's session concept. Keep FeatureStore as a generic data provider.

### Extensions for Volume Profile Strategy

Volume profile needs price-volume distribution (histogram of volume at each price level). This requires:

**Option A: Compute in-strategy (recommended).** The strategy bins `store.closes` and `store.volumes` into a histogram at evaluation time. With 500 samples this is a trivial numpy operation (~50 price bins). No FeatureStore changes.

**Why Option A:** Volume profile binning depends on strategy-specific parameters (bin width, lookback window). Putting this in FeatureStore would mean the store needs to know about strategy config. Keep the store generic.

### One Justified FeatureStore Addition: Timestamps Property

The FeatureStore tracks `_timestamps` internally but does not expose a public property for them. The VWAP strategy needs timestamps to determine session boundaries. Add:

```python
@property
def timestamps(self) -> list[datetime]:
    """Sample timestamps."""
    return list(self._timestamps)
```

This is a trivial accessor for data already collected. No new data ingestion needed.

## Patterns to Follow

### Pattern 1: Params Dataclass Per Strategy
**What:** Each strategy defines a frozen `@dataclass` for its parameters with sensible defaults. Config dict is parsed in `__init__` to construct the params object.
**When:** Every new strategy.
**Why:** Type safety, IDE support, clear documentation of what is tunable. All existing strategies follow this.

### Pattern 2: Cooldown via Bar Counter
**What:** Track `_bars_since_signal` to enforce minimum time between signals from the same strategy on the same instrument.
**When:** Every strategy that could fire repeatedly on similar conditions.
**Why:** Prevents signal flooding. The alpha combiner deduplicates, but per-strategy cooldown is the first line of defense.

### Pattern 3: Conviction as a Continuous Score
**What:** Conviction (0.0-1.0) should scale with signal strength, not be binary. Use multiple confirming factors to build conviction.
**When:** Always.
**Why:** The alpha combiner and portfolio router use conviction thresholds. A strategy that only emits 0.0 or 1.0 conviction defeats the purpose of the aggregation layer.

### Pattern 4: Portfolio A Routing via suggested_target
**What:** High-conviction, short-horizon signals should set `suggested_target=PortfolioTarget.A`. The portfolio router in the alpha layer makes the final decision, but the strategy's suggestion is weighted.
**When:** Strategies with time-sensitive signals (funding arb near settlement, orderbook sweeps, breakouts).
**Why:** Routing rules in `default.yaml` already map `FUNDING_ARB` and `ORDERBOOK_IMBALANCE` sources to Portfolio A. Strategies should also set `suggested_target` when conviction is high enough.

### Pattern 5: Strategy-Specific min_history
**What:** Override `min_history` to return the minimum sample count before `evaluate()` can produce meaningful signals.
**When:** Any strategy using indicators with lookback periods.
**Why:** The orchestrator checks `store.sample_count < strategy.min_history` before calling evaluate. Without this, strategies produce garbage signals during warm-up.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Reaching Outside the Strategy Contract
**What:** Strategies accessing Redis directly, reading other strategies' state, or modifying the FeatureStore.
**Why bad:** Breaks the isolation model. Strategies are pure functions of (snapshot, store) -> signals.
**Instead:** If cross-strategy information is needed, it belongs in the alpha combiner (which already does regime detection and conflict resolution).

### Anti-Pattern 2: Hardcoded Thresholds
**What:** Magic numbers embedded in strategy code instead of the params dataclass and YAML config.
**Why bad:** Cannot tune per-instrument. Cannot adjust without code changes and redeployment.
**Instead:** Every threshold goes in the params dataclass with a default, and is overridable via YAML.

### Anti-Pattern 3: Heavy Computation in evaluate()
**What:** Strategies that do expensive work (large matrix operations, optimization) on every 30-second tick.
**Why bad:** Strategies run sequentially per instrument (though instruments are processed as snapshots arrive). A slow strategy delays all subsequent strategies for that instrument.
**Instead:** Cache intermediate computations. Use the FeatureStore's rolling arrays (already numpy) for vectorized operations. Profile and keep evaluate() under 10ms.

### Anti-Pattern 4: Strategy-Level State Leaking Across Instruments
**What:** A strategy maintaining state that bleeds between instruments.
**Why bad:** Each instrument has its own strategy instance (per `build_strategies_for_instrument()`). But if a developer accidentally uses class-level mutable state, it could leak.
**Instead:** All state in `self._*` instance attributes, initialized in `__init__`.

## Data Flow

### New Strategy Signal Flow (Unchanged from Existing)

```
Coinbase WS/REST
      |
      v
Ingestion Agent --> MarketSnapshot --> Redis stream:market_snapshots
                                            |
                                            v
                                    Signals Agent (main.py)
                                            |
                         +------------------+------------------+
                         |                  |                  |
                    ETH-PERP           BTC-PERP           SOL-PERP  ...
                         |                  |                  |
                   FeatureStore        FeatureStore        FeatureStore
                         |                  |                  |
              [momentum, mean_rev,   [momentum, mean_rev,   [all strategies
               funding_arb, vwap,     funding_arb, vwap,     for this
               vol_profile, ...]      vol_profile, ...]      instrument]
                         |                  |                  |
                    StandardSignal     StandardSignal     StandardSignal
                         |                  |                  |
                         +------------------+------------------+
                                            |
                                            v
                                    Redis stream:signals
                                            |
                                            v
                                    Alpha Combiner (untouched)
```

Key points:
- Each instrument has its own FeatureStore instance (already exists)
- Each instrument has its own set of strategy instances with merged per-instrument config (already exists)
- Strategies run per-instrument as snapshots arrive (not in parallel across strategies for the same instrument, but instruments are naturally interleaved)
- Error in one strategy does not affect others (existing try/except in main.py)

## Build Order: Which Strategies and Improvements Should Come First

### Phase 1: Shared Infrastructure (Do First)

1. **Add `VWAP` and `VOLUME_PROFILE` to `SignalSource` enum** -- 2 lines, unblocks VWAP and volume profile strategies
2. **Add `timestamps` property to `FeatureStore`** -- 3 lines, unblocks VWAP session logic
3. **Per-instrument tuning for existing 5 strategies** -- config-only changes in `configs/strategies/*.yaml`, no code changes. This is pure YAML work that makes existing strategies smarter immediately.

Rationale: These are tiny changes that unblock everything else. Per-instrument tuning is the highest-ROI work because it makes the existing 5 strategies better across all 5 instruments with zero code risk.

### Phase 2: New Strategies That Use Existing Data

4. **Funding arb strategy** -- FeatureStore already has `funding_rates`, `index_prices`, `closes`. Config YAML already exists (`funding_arb.yaml`). `SignalSource.FUNDING_ARB` already in enum. Routing rule already maps to Portfolio A. This is the most "ready to build" new strategy.
5. **Orderbook imbalance strategy** -- FeatureStore already has `orderbook_imbalances`. Config YAML already exists. `SignalSource.ORDERBOOK_IMBALANCE` already in enum. Routing rule maps to Portfolio A. Second-most ready.

Rationale: These two strategies have pre-existing enum slots, config files, and routing rules. The data they need is already in the FeatureStore. They are the lowest-risk new strategies to add.

### Phase 3: New Strategies That Need Derived Data

6. **VWAP strategy** -- Needs to compute VWAP from closes + volumes (both in FeatureStore). Needs timestamps for session boundaries. Moderate complexity.
7. **Volume profile strategy** -- Needs to compute price-volume histogram from closes + volumes. Pure numpy computation. Moderate complexity.

Rationale: These strategies need in-strategy computation beyond what the FeatureStore provides directly. Not hard, but more implementation work than funding arb and orderbook imbalance.

### Phase 4: Existing Strategy Improvements

8. **Improve all 5 existing strategies** -- Adaptive parameters, better conviction models, smarter stops. Each strategy improvement is independent and can be done in any order.

Rationale: Improving existing strategies is valuable but higher-risk than adding new ones (changes to working code vs. new code). Per-instrument tuning in Phase 1 captures most of the "make existing strategies smarter" value. Code-level improvements are the refinement layer.

### Phase 5: Cross-Cutting Quality

9. **Dual portfolio routing for all strategies** -- Ensure every strategy considers Portfolio A routing for high-conviction signals
10. **Cross-strategy signal quality** -- Instrument-specific conviction calibration

Rationale: These require all strategies to be in place first, since they affect signal quality and routing across the full strategy set.

## Whether Shared Infrastructure Changes Are Needed Before Strategy Work

**Almost none.** The architecture is designed for strategy extensibility:

| Infrastructure | Change Needed | Blocks |
|---------------|--------------|--------|
| `SignalStrategy` base class | None | Nothing |
| `StandardSignal` contract | None | Nothing |
| `FeatureStore` | Add `timestamps` property (3 lines) | VWAP strategy only |
| `SignalSource` enum | Add `VWAP`, `VOLUME_PROFILE` values (2 lines) | VWAP, volume profile strategies |
| `main.py` orchestrator | Add entries to `STRATEGY_CLASSES` dict | New strategies (1 line each) |
| Config loading | None | Nothing |
| Alpha combiner | None | Nothing |
| Risk/execution/reconciliation | None | Nothing |
| Portfolio routing rules | Optional: add VWAP/VOLUME_PROFILE to routing rules in default.yaml | Portfolio A routing for new strategies |

Total shared infrastructure: ~10 lines of code changes, all additive, all backward-compatible.

## Scalability Considerations

| Concern | Current (5 strategies x 5 instruments) | After (9 strategies x 5 instruments) |
|---------|----------------------------------------|---------------------------------------|
| Strategy instances | 25 (max) | 45 (max) |
| evaluate() calls per snapshot | 5 (one instrument's strategies) | 9 (one instrument's strategies) |
| Memory (FeatureStore) | 5 stores x 500 samples x ~8 series | Unchanged (stores are per-instrument, not per-strategy) |
| Latency per snapshot | ~5-50ms (5 strategies) | ~10-90ms (9 strategies, sequential) |
| Signal volume | Low (conservative thresholds) | Higher (more strategies + tuned thresholds) |

The latency increase from 5 to 9 strategies is linear and bounded. At 30-second sample intervals, even 100ms per snapshot is negligible. The alpha combiner already handles signal deduplication and conflict resolution, so higher signal volume is manageable.

If latency ever becomes a concern (unlikely), strategies could be parallelized within an instrument using `asyncio.TaskGroup`, but current sequential execution is simpler and sufficient.

## Sources

- Direct code analysis of the existing codebase (HIGH confidence -- primary source)
  - `agents/signals/strategies/base.py` -- strategy interface
  - `agents/signals/main.py` -- orchestration and per-instrument instantiation
  - `agents/signals/feature_store.py` -- available data series
  - `libs/common/config.py` -- config loading with per-instrument merge
  - `libs/common/models/enums.py` -- SignalSource enum with existing slots
  - `libs/common/models/signal.py` -- StandardSignal contract
  - `configs/strategies/regime_trend.yaml` -- exemplar per-instrument config
  - `configs/strategies/funding_arb.yaml` -- pre-existing config for new strategy
  - `configs/strategies/orderbook_imbalance.yaml` -- pre-existing config for new strategy
  - `configs/default.yaml` -- routing rules, instrument specs
