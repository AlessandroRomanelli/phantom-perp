# Phase 6: Config and State Foundation - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

The ingestion layer reads instrument configuration from YAML and manages per-instrument state instead of relying on hardcoded single-instrument constants. All 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY) are defined in config. No hardcoded instrument constants remain in constants.py. All callers across the codebase are updated.

</domain>

<decisions>
## Implementation Decisions

### YAML instruments structure
- **D-01:** Replace singular `instrument:` block in default.yaml with an `instruments:` list containing all 5 perp contracts
- **D-02:** Each instrument entry includes: id, base_currency, quote_currency, tick_size, min_order_size
- **D-03:** WS product IDs are derived by convention (`{id}-INTX`, e.g. `ETH-PERP` → `ETH-PERP-INTX`) — no explicit WS mapping in YAML
- **D-04:** `ACTIVE_INSTRUMENT_IDS` list in constants.py is replaced by reading from YAML config

### Constants removal and caller migration
- **D-05:** Remove `INSTRUMENT_ID`, `BASE_CURRENCY`, `QUOTE_CURRENCY`, `TICK_SIZE`, `MIN_ORDER_SIZE` from constants.py entirely — no compatibility shim
- **D-06:** All ~20 callers across the codebase (ingestion, signals strategies, execution, reconciliation, tests) are updated in this phase to use config-driven values
- **D-07:** A config accessor function/module provides per-instrument metadata at runtime (loaded from YAML at startup)

### Per-instrument state lifecycle
- **D-08:** `Dict[str, IngestionState]` created eagerly at startup from the instruments list in config
- **D-09:** Config changes (adding/removing instruments) require agent restart — no dynamic reloading
- **D-10:** Each IngestionState is independent per instrument — no shared mutable state between instruments

### Claude's Discretion
- Config accessor API design (function vs class vs module-level registry)
- Whether IngestionState stores its own instrument_id field
- Test fixture approach for multi-instrument state
- Order of file migration when removing constants

</decisions>

<specifics>
## Specific Ideas

No specific requirements — standard approaches apply. The WS convention (`{id}-INTX`) is already proven for ETH-PERP-INTX in the current codebase.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Instrument config
- `configs/default.yaml` — Current singular `instrument:` block (lines 1-6) that must become `instruments:` list
- `libs/common/constants.py` — Hardcoded constants to remove (lines 7-11, 13-19)
- `libs/common/config.py` — Config loading infrastructure, `load_yaml_config()` and `get_settings()`

### Ingestion state
- `agents/ingestion/state.py` — Current single-instrument `IngestionState` dataclass
- `agents/ingestion/main.py` — Entrypoint creating single `IngestionState()` (line 40)
- `agents/ingestion/normalizer.py` — Hardcoded `INSTRUMENT_ID` import (line 14, used at line 65)

### Data sources
- `agents/ingestion/sources/ws_market_data.py` — Hardcoded `WS_PRODUCT_ID = "ETH-PERP-INTX"` (line 24)
- `agents/ingestion/sources/candles.py` — Imports `INSTRUMENT_ID` for REST calls (line 15, used at line 60)
- `agents/ingestion/sources/funding_rate.py` — Imports `INSTRUMENT_ID` for REST calls (line 17, used at lines 44, 84)

### Non-ingestion callers (must also migrate)
- `agents/signals/strategies/momentum.py` — INSTRUMENT_ID import (line 25)
- `agents/signals/strategies/mean_reversion.py` — INSTRUMENT_ID import (line 26)
- `agents/signals/strategies/correlation.py` — INSTRUMENT_ID import (line 22)
- `agents/signals/strategies/regime_trend.py` — INSTRUMENT_ID import (line 27)
- `agents/signals/strategies/liquidation_cascade.py` — INSTRUMENT_ID import (line 25)
- `agents/signals/main.py` — ACTIVE_INSTRUMENT_IDS import (line 29)
- `agents/execution/main.py` — INSTRUMENT_ID import (line 30)
- `agents/reconciliation/main.py` — INSTRUMENT_ID import (line 31)
- `libs/coinbase/rest_client.py` — INSTRUMENT_ID import (line 27)
- Multiple test files in `agents/signals/tests/` — INSTRUMENT_ID imports

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `load_yaml_config()` in `libs/common/config.py` — already loads default.yaml, can be extended to parse instruments list
- `load_strategy_config_for_instrument()` — existing per-instrument config merge pattern, proven approach
- `ACTIVE_INSTRUMENT_IDS` in constants.py — already lists all 5 instruments, validates the target set
- `AppSettings.yaml_config` — dict field that holds parsed YAML, instruments config can live here

### Established Patterns
- Strategy configs use `instruments:` key with per-instrument overrides — same pattern should apply to default.yaml
- `get_settings()` loads YAML at startup and returns immutable settings — instruments config follows this pattern
- `IngestionState` is a plain dataclass with no instrument awareness — adding instrument_id field is trivial

### Integration Points
- `build_snapshot(state)` in normalizer — must accept instrument parameter instead of importing constant
- `on_ws_update()` callback in main.py — must know which instrument triggered the update
- `run_ws_market_data()` — must derive WS product IDs from config instruments list
- `run_all_candle_pollers()` and `run_funding_poller()` — must loop over instruments from config
- Signal strategies receive instrument_id via `evaluate()` method — already per-instrument aware

</code_context>

<deferred>
## Deferred Ideas

- Per-instrument rate limiting for REST pollers — monitor for issues before adding complexity (noted in REQUIREMENTS.md Out of Scope)
- Dynamic instrument reload without restart — unnecessary complexity for 5 fixed instruments

</deferred>

---

*Phase: 06-config-state-foundation*
*Context gathered: 2026-03-22*
