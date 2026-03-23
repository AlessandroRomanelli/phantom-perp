# Phase 6: Config and State Foundation - Research

**Researched:** 2026-03-22
**Domain:** YAML configuration migration, per-instrument state management, constant removal
**Confidence:** HIGH

## Summary

This phase is a surgical refactoring: replace hardcoded single-instrument constants with config-driven values from `default.yaml`, and create per-instrument `IngestionState` dictionaries. The scope is well-defined with ~25 call sites across the codebase importing `INSTRUMENT_ID`, `BASE_CURRENCY`, `QUOTE_CURRENCY`, `TICK_SIZE`, or `MIN_ORDER_SIZE` from `constants.py`.

The existing codebase already has strong config infrastructure (`load_yaml_config()`, `load_strategy_config_for_instrument()`, `get_settings()`) and the `AppSettings.yaml_config` dict field that holds parsed YAML. The new instruments list just needs to be parsed from this existing dict and exposed via a clean accessor API.

The risk is low -- this is pure plumbing. The primary complexity is the breadth of callers that need updating (ingestion sources, signal strategies, execution, reconciliation, REST client defaults, utility functions, and ~12 test files). No new libraries needed. No architectural changes.

**Primary recommendation:** Create an `InstrumentConfig` dataclass and a module-level registry in `libs/common/instruments.py` that loads from YAML at startup. All callers replace constant imports with registry lookups. Remove the 5 hardcoded constants from `constants.py`. Update `IngestionState` to carry its `instrument_id`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Replace singular `instrument:` block in default.yaml with an `instruments:` list containing all 5 perp contracts
- **D-02:** Each instrument entry includes: id, base_currency, quote_currency, tick_size, min_order_size
- **D-03:** WS product IDs are derived by convention (`{id}-INTX`, e.g. `ETH-PERP` -> `ETH-PERP-INTX`) -- no explicit WS mapping in YAML
- **D-04:** `ACTIVE_INSTRUMENT_IDS` list in constants.py is replaced by reading from YAML config
- **D-05:** Remove `INSTRUMENT_ID`, `BASE_CURRENCY`, `QUOTE_CURRENCY`, `TICK_SIZE`, `MIN_ORDER_SIZE` from constants.py entirely -- no compatibility shim
- **D-06:** All ~20 callers across the codebase (ingestion, signals strategies, execution, reconciliation, tests) are updated in this phase to use config-driven values
- **D-07:** A config accessor function/module provides per-instrument metadata at runtime (loaded from YAML at startup)
- **D-08:** `Dict[str, IngestionState]` created eagerly at startup from the instruments list in config
- **D-09:** Config changes (adding/removing instruments) require agent restart -- no dynamic reloading
- **D-10:** Each IngestionState is independent per instrument -- no shared mutable state between instruments

### Claude's Discretion
- Config accessor API design (function vs class vs module-level registry)
- Whether IngestionState stores its own instrument_id field
- Test fixture approach for multi-instrument state
- Order of file migration when removing constants

### Deferred Ideas (OUT OF SCOPE)
- Per-instrument rate limiting for REST pollers -- monitor for issues before adding complexity
- Dynamic instrument reload without restart -- unnecessary complexity for 5 fixed instruments
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MCFG-01 | default.yaml supports a list of active instruments with per-instrument metadata (tick_size, min_order_size, base_currency) | YAML structure defined in D-01/D-02; instrument metadata fields known from current constants.py |
| MCFG-02 | Remove hardcoded single-instrument constants from constants.py -- use config-driven values | Full caller inventory identified (21 files); accessor API design researched |
| MSTA-01 | IngestionState is managed per-instrument via Dict[str, IngestionState] in main.py | Current single IngestionState() at line 40 of main.py; eager creation pattern from D-08 |
| MSTA-02 | Normalizer builds MarketSnapshot with correct instrument ID from parameter (not hardcoded constant) | normalizer.py line 14 imports INSTRUMENT_ID, line 65 uses it; build_snapshot needs instrument param |
</phase_requirements>

## Standard Stack

No new libraries required. This phase uses only existing project dependencies.

### Core (already in project)
| Library | Version | Purpose | Role in This Phase |
|---------|---------|---------|-------------------|
| PyYAML | 6+ | YAML parsing | Already used by `load_yaml_config()` -- no changes needed |
| pydantic-settings | 2.2+ | Settings management | `AppSettings.yaml_config` dict already holds parsed YAML |
| dataclasses | stdlib | Data models | New `InstrumentConfig` dataclass |

### No New Installs
```bash
# No new packages needed
```

## Architecture Patterns

### Recommended: Module-Level Registry in `libs/common/instruments.py`

**What:** A new module that loads instrument configs from YAML at startup and provides lookup functions.

**Why this design (Claude's Discretion decision):**
- Module-level registry (not class, not function-only) matches the existing pattern in `libs/common/config.py` where `get_settings()` is a module-level function
- Avoids passing instrument configs through every function signature
- Singleton-like behavior via module-level `_registry` dict, initialized lazily on first access
- Frozen dataclass for `InstrumentConfig` matches `StandardSignal` pattern (frozen=True, slots=True)

**Pattern:**
```python
# libs/common/instruments.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

@dataclass(frozen=True, slots=True)
class InstrumentConfig:
    """Per-instrument metadata loaded from YAML config."""
    id: str
    base_currency: str
    quote_currency: str
    tick_size: Decimal
    min_order_size: Decimal

    @property
    def ws_product_id(self) -> str:
        """Derive WS product ID by convention: {id}-INTX."""
        return f"{self.id}-INTX"


_registry: dict[str, InstrumentConfig] = {}


def load_instruments(yaml_config: dict[str, Any]) -> None:
    """Load instrument configs from parsed YAML into module registry."""
    _registry.clear()
    for entry in yaml_config.get("instruments", []):
        cfg = InstrumentConfig(
            id=entry["id"],
            base_currency=entry["base_currency"],
            quote_currency=entry["quote_currency"],
            tick_size=Decimal(str(entry["tick_size"])),
            min_order_size=Decimal(str(entry["min_order_size"])),
        )
        _registry[cfg.id] = cfg


def get_instrument(instrument_id: str) -> InstrumentConfig:
    """Get config for a specific instrument. Raises KeyError if not found."""
    return _registry[instrument_id]


def get_all_instruments() -> list[InstrumentConfig]:
    """Get all configured instruments."""
    return list(_registry.values())


def get_active_instrument_ids() -> list[str]:
    """Get list of active instrument IDs (replaces ACTIVE_INSTRUMENT_IDS constant)."""
    return list(_registry.keys())
```

### IngestionState with instrument_id Field

**Claude's Discretion decision:** YES, store `instrument_id` on IngestionState.

**Rationale:** When `build_snapshot(state)` is called, it needs the instrument ID. Storing it on the state object means no extra parameter threading. The state is already mutable (not frozen), so adding a required field at construction is natural.

```python
@dataclass
class IngestionState:
    instrument_id: str  # Required, set at construction
    # ... existing fields unchanged ...
```

### YAML Structure for `instruments:`
```yaml
instruments:
  - id: "ETH-PERP"
    base_currency: "ETH"
    quote_currency: "USDC"
    tick_size: 0.01
    min_order_size: 0.0001
  - id: "BTC-PERP"
    base_currency: "BTC"
    quote_currency: "USDC"
    tick_size: 0.01
    min_order_size: 0.00001
  - id: "SOL-PERP"
    base_currency: "SOL"
    quote_currency: "USDC"
    tick_size: 0.001
    min_order_size: 0.01
  - id: "QQQ-PERP"
    base_currency: "QQQ"
    quote_currency: "USDC"
    tick_size: 0.01
    min_order_size: 0.001
  - id: "SPY-PERP"
    base_currency: "SPY"
    quote_currency: "USDC"
    tick_size: 0.01
    min_order_size: 0.001
```

**Note:** The exact `tick_size` and `min_order_size` values for BTC, SOL, QQQ, SPY perpetuals on Coinbase INTX need verification against the exchange API. The values above are reasonable defaults but should be confirmed. ETH-PERP values come from the current `constants.py`.

### Recommended Migration Order (Claude's Discretion)

1. **YAML first** -- add `instruments:` list to `default.yaml` (keep old `instrument:` block temporarily for backward compat during migration)
2. **InstrumentConfig + registry** -- create `libs/common/instruments.py` with `load_instruments()` and lookup functions
3. **Wire into startup** -- call `load_instruments()` from `get_settings()` after YAML is loaded
4. **Ingestion layer** -- update `IngestionState`, `normalizer.py`, `main.py`, `ws_market_data.py`, `candles.py`, `funding_rate.py`
5. **Non-ingestion callers** -- update `rest_client.py`, signal strategies, `execution/main.py`, `reconciliation/main.py`, `utils.py`
6. **Test files** -- update all test imports
7. **Remove constants** -- delete the 5 instrument constants and `ACTIVE_INSTRUMENT_IDS` from `constants.py`; remove old `instrument:` block from YAML

### Recommended Project Structure Changes
```
libs/common/
  instruments.py          # NEW: InstrumentConfig dataclass + registry
  constants.py            # MODIFIED: remove 6 instrument-related constants
  config.py               # MODIFIED: call load_instruments() in get_settings()
  utils.py                # MODIFIED: remove TICK_SIZE/MIN_ORDER_SIZE import defaults
configs/
  default.yaml            # MODIFIED: instrument: -> instruments: list
agents/ingestion/
  state.py                # MODIFIED: add instrument_id field
  normalizer.py           # MODIFIED: read instrument from state, not constant
  main.py                 # MODIFIED: Dict[str, IngestionState]
  sources/ws_market_data.py  # MODIFIED: remove WS_PRODUCT_ID constant
  sources/candles.py         # MODIFIED: accept instrument_id parameter
  sources/funding_rate.py    # MODIFIED: accept instrument_id parameter
```

### Anti-Patterns to Avoid
- **Passing instrument_id as a string everywhere:** Use `InstrumentConfig` objects where metadata (tick_size, etc.) is also needed. Use plain `str` only where just the ID is needed.
- **Lazy-loading from YAML in each caller:** Load once at startup, access via registry. Don't re-parse YAML on each call.
- **Removing the old `instrument:` key from YAML before all callers are migrated:** Keep it during transition; remove in the final cleanup step.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML parsing | Custom config parser | Existing `load_yaml_config()` | Already proven, handles file-not-found |
| Decimal conversion from YAML | Inline `Decimal(str(x))` everywhere | `InstrumentConfig.__init__` | Single conversion point, consistent |
| Instrument ID validation | Ad-hoc checks scattered in code | `get_instrument()` raising `KeyError` | Fail fast at one place |

## Common Pitfalls

### Pitfall 1: YAML Floats vs Decimal Precision
**What goes wrong:** YAML parses `0.01` as Python `float(0.01)`, which has floating-point imprecision. If you do `Decimal(0.01)` you get `Decimal('0.01000000000000000020816681711721685228163...')`.
**Why it happens:** YAML spec has no Decimal type; all numbers are float.
**How to avoid:** Always convert via `Decimal(str(value))` -- convert to string first, then to Decimal. This is already the project convention.
**Warning signs:** Tests failing with precision mismatches on tick_size or min_order_size.

### Pitfall 2: Circular Import with instruments.py
**What goes wrong:** If `instruments.py` imports from `config.py` and `config.py` imports from `instruments.py`, circular import.
**Why it happens:** Temptation to have `get_settings()` automatically call `load_instruments()`.
**How to avoid:** Keep `instruments.py` independent -- it takes a `dict[str, Any]` parameter, not a `Settings` object. Call `load_instruments(settings.yaml_config)` from the caller (e.g., agent `main.py`), or wire it inside `get_settings()` carefully (since `instruments.py` does not import `config.py`).

### Pitfall 3: utils.py Default Parameter Values
**What goes wrong:** `round_to_tick(price, tick_size=TICK_SIZE)` and `round_size(size, min_size=MIN_ORDER_SIZE)` use constants as default parameter values. Removing the constants breaks the function signatures.
**Why it happens:** Default parameter values are evaluated at import time.
**How to avoid:** Change defaults to `None` and look up from registry inside the function body, OR require callers to always pass the value explicitly. The latter is safer for multi-instrument use (each instrument has different tick_size).
**Recommendation:** Remove defaults entirely -- callers MUST pass tick_size/min_order_size explicitly. This prevents bugs where the wrong instrument's tick_size is used.

### Pitfall 4: REST Client Default Parameters
**What goes wrong:** `rest_client.py` methods like `get_candles(instrument_id=INSTRUMENT_ID)` use the constant as a default parameter.
**Why it happens:** Convenient single-instrument design.
**How to avoid:** Remove the default -- make `instrument_id` a required parameter on all REST client methods. This forces callers to be explicit about which instrument they're querying.

### Pitfall 5: Test Files Using INSTRUMENT_ID as Expected Value
**What goes wrong:** Tests assert `snapshot.instrument == INSTRUMENT_ID`. After removing the constant, tests need a replacement.
**Why it happens:** Tests import the constant for assertion values.
**How to avoid:** Use string literals in tests (`"ETH-PERP"`) or define a `TEST_INSTRUMENT_ID = "ETH-PERP"` in a test conftest. String literals are simpler and more explicit.

### Pitfall 6: Forgetting the funding_rate.py `_publish_funding_update` Call
**What goes wrong:** `_publish_funding_update()` at line 84 uses `INSTRUMENT_ID` directly (not from a parameter). Easy to miss since it's a private helper.
**How to avoid:** Grep for ALL usages of removed constants, not just the import lines. The research above has already identified this.

## Code Examples

### Caller Migration Pattern: Ingestion normalizer
```python
# BEFORE (normalizer.py)
from libs.common.constants import INSTRUMENT_ID

def build_snapshot(state: IngestionState) -> MarketSnapshot | None:
    ...
    return MarketSnapshot(instrument=INSTRUMENT_ID, ...)

# AFTER
def build_snapshot(state: IngestionState) -> MarketSnapshot | None:
    ...
    return MarketSnapshot(instrument=state.instrument_id, ...)
```

### Caller Migration Pattern: REST client defaults
```python
# BEFORE (rest_client.py)
from libs.common.constants import INSTRUMENT_ID

async def get_candles(self, instrument_id: str = INSTRUMENT_ID, ...) -> ...:

# AFTER -- no default, required parameter
async def get_candles(self, instrument_id: str, ...) -> ...:
```

### Caller Migration Pattern: utils.py
```python
# BEFORE
from libs.common.constants import MIN_ORDER_SIZE, TICK_SIZE

def round_to_tick(price: Decimal, tick_size: Decimal = TICK_SIZE) -> Decimal:

# AFTER -- no default, caller must provide
def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
```

### Ingestion main.py: Per-Instrument State Dict
```python
# BEFORE
state = IngestionState()

# AFTER
from libs.common.instruments import get_all_instruments

instruments = get_all_instruments()
states: dict[str, IngestionState] = {
    inst.id: IngestionState(instrument_id=inst.id)
    for inst in instruments
}
```

### Test Fixture Pattern (Claude's Discretion)
```python
# conftest.py or inline in test files
TEST_INSTRUMENT_ID = "ETH-PERP"

# Or for multi-instrument tests:
@pytest.fixture
def instrument_configs():
    """Load instrument registry for tests."""
    from libs.common.instruments import load_instruments
    load_instruments({
        "instruments": [
            {"id": "ETH-PERP", "base_currency": "ETH", "quote_currency": "USDC",
             "tick_size": 0.01, "min_order_size": 0.0001},
        ]
    })
```

## Complete Caller Inventory

All files that import instrument-related constants from `constants.py`:

### INSTRUMENT_ID imports (21 files)
| File | Usage | Migration Approach |
|------|-------|--------------------|
| `agents/ingestion/normalizer.py` | `instrument=INSTRUMENT_ID` in snapshot | Use `state.instrument_id` |
| `agents/ingestion/sources/candles.py` | `instrument_id=INSTRUMENT_ID` in REST call | Accept instrument_id param |
| `agents/ingestion/sources/funding_rate.py` | `instrument_id=INSTRUMENT_ID` in REST call + publish | Accept instrument_id param |
| `agents/ingestion/sources/ws_market_data.py` | `WS_PRODUCT_ID = "ETH-PERP-INTX"` (not imported, hardcoded) | Derive from config |
| `agents/signals/strategies/momentum.py` | Backward compat default | Use snapshot.instrument |
| `agents/signals/strategies/mean_reversion.py` | Same | Use snapshot.instrument |
| `agents/signals/strategies/correlation.py` | Same | Use snapshot.instrument |
| `agents/signals/strategies/regime_trend.py` | Same | Use snapshot.instrument |
| `agents/signals/strategies/liquidation_cascade.py` | Same | Use snapshot.instrument |
| `agents/signals/main.py` | `ACTIVE_INSTRUMENT_IDS` for instrument list | Use `get_active_instrument_ids()` |
| `agents/execution/main.py` | Order context | Use order's instrument field |
| `agents/reconciliation/main.py` | Position queries | Use config instrument list |
| `libs/coinbase/rest_client.py` | Default parameter values | Make instrument_id required |
| `libs/common/utils.py` | `TICK_SIZE`, `MIN_ORDER_SIZE` as defaults | Remove defaults, require explicit |
| `agents/risk/position_sizer.py` | `MIN_ORDER_SIZE` | Look up from instrument config |
| `agents/risk/tests/test_computations.py` | `MIN_ORDER_SIZE` in tests | Use literal or fixture |
| `agents/execution/stop_loss_manager.py` | `TICK_SIZE` | Look up from instrument config |
| `agents/execution/retry_handler.py` | `TICK_SIZE` (lazy import) | Look up from instrument config |
| `agents/execution/algo_selector.py` | `TICK_SIZE` | Look up from instrument config |

### Test files importing INSTRUMENT_ID (9 files)
| File | Migration |
|------|-----------|
| `agents/signals/tests/test_main.py` | String literal or fixture |
| `agents/signals/tests/test_momentum.py` | String literal |
| `agents/signals/tests/test_mean_reversion.py` | String literal |
| `agents/signals/tests/test_correlation.py` | String literal |
| `agents/signals/tests/test_liquidation_cascade.py` | String literal |
| `agents/signals/tests/test_regime_trend.py` | String literal |
| `agents/signals/tests/test_orderbook_imbalance.py` | String literal |
| `agents/signals/tests/test_vwap.py` | String literal |
| `agents/signals/tests/test_feature_store.py` | String literal |
| `agents/ingestion/tests/test_normalizer.py` | String literal |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | `pyproject.toml` (pytest section) |
| Quick run command | `python3 -m pytest agents/ingestion/tests/ -x -q` |
| Full suite command | `python3 -m pytest agents/ libs/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MCFG-01 | instruments list in YAML with metadata | unit | `python3 -m pytest libs/common/tests/test_instruments.py -x` | No -- Wave 0 |
| MCFG-02 | No hardcoded instrument constants in constants.py | unit (grep-based) | `grep -c "INSTRUMENT_ID\|BASE_CURRENCY\|QUOTE_CURRENCY\|TICK_SIZE\|MIN_ORDER_SIZE" libs/common/constants.py` | Manual verification |
| MSTA-01 | Dict[str, IngestionState] in main.py | unit | `python3 -m pytest agents/ingestion/tests/test_main.py -x` | No -- Wave 0 |
| MSTA-02 | Normalizer uses instrument param not constant | unit | `python3 -m pytest agents/ingestion/tests/test_normalizer.py -x` | Yes -- needs update |

### Sampling Rate
- **Per task commit:** `python3 -m pytest agents/ingestion/tests/ -x -q`
- **Per wave merge:** `python3 -m pytest agents/ libs/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `libs/common/tests/test_instruments.py` -- covers MCFG-01 (InstrumentConfig loading, registry lookup, ws_product_id derivation)
- [ ] Update `agents/ingestion/tests/test_normalizer.py` -- covers MSTA-02 (instrument from state, not constant)
- [ ] Instrument registry fixture in `conftest.py` if needed for multi-file test support

## Open Questions

1. **Exact tick_size/min_order_size for BTC, SOL, QQQ, SPY on Coinbase INTX**
   - What we know: ETH-PERP values from constants.py (tick_size=0.01, min_order_size=0.0001)
   - What's unclear: Exact values for other 4 instruments on Coinbase INTX
   - Recommendation: Use reasonable defaults in YAML, verify against Coinbase INTX API (`GET /api/v1/instruments/{instrument_id}`) during implementation or paper trading. The REST client already has an `InstrumentResponse` model, suggesting the API endpoint exists.

2. **Whether `load_instruments()` should be called inside `get_settings()` or by each agent**
   - What we know: `get_settings()` already loads YAML; instruments data is in the same YAML
   - What's unclear: Whether coupling instrument registry init to settings init is clean
   - Recommendation: Call `load_instruments(yaml_config)` inside `get_settings()` after YAML is loaded. This ensures instruments are always available when settings are loaded. No circular import risk since `instruments.py` does not import `config.py`.

## Sources

### Primary (HIGH confidence)
- `libs/common/constants.py` -- current hardcoded constants to remove (lines 7-19)
- `libs/common/config.py` -- existing config loading infrastructure
- `configs/default.yaml` -- current YAML structure
- `agents/ingestion/state.py` -- current IngestionState dataclass
- `agents/ingestion/main.py` -- current single-state creation pattern
- `agents/ingestion/normalizer.py` -- current INSTRUMENT_ID usage

### Secondary (HIGH confidence)
- Grep results across codebase -- complete caller inventory verified against source files
- `.planning/phases/06-config-state-foundation/06-CONTEXT.md` -- locked decisions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, pure refactoring of existing code
- Architecture: HIGH -- patterns follow existing project conventions, all source files reviewed
- Pitfalls: HIGH -- identified from direct code inspection of all affected files
- Caller inventory: HIGH -- grep-verified across entire codebase

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- no external dependencies changing)
