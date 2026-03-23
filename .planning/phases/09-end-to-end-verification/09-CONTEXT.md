# Phase 9: End-to-End Verification - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Verify that all 5 instruments (ETH-PERP, BTC-PERP, SOL-PERP, QQQ-PERP, SPY-PERP) produce MarketSnapshots that flow through the full ingestion pipeline and are consumed by the signals agent. This phase produces automated tests and dashboard enhancements — no new pipeline features.

</domain>

<decisions>
## Implementation Decisions

### Verification artifacts
- **D-01:** Automated integration tests covering the full wiring (ingestion → snapshot → signals FeatureStore) PLUS dashboard enhancement showing per-instrument status
- **D-02:** No standalone verification script — the dashboard serves as the live environment health check

### Test scope — ingestion wiring
- **D-03:** Integration tests in `agents/ingestion/tests/` that verify `on_ws_update()` with 5 different instrument states produces 5 snapshots with correct instrument fields (ME2E-01)
- **D-04:** Tests feed known instrument data through the normalizer and assert `snapshot.instrument` matches the expected instrument ID for each of the 5 instruments

### Test scope — signals consumption
- **D-05:** Integration tests in `agents/signals/tests/` that verify the signals agent routes snapshots to the correct per-instrument FeatureStores (ME2E-02)
- **D-06:** Tests verify that FeatureStore `sample_count` is non-zero for all 5 instruments after feeding snapshots
- **D-07:** Tests verify that strategies fire (evaluate returns signals) for instruments where the strategy is enabled per `strategy_matrix.yaml`

### Dashboard enhancement
- **D-08:** Add per-instrument snapshot table to existing `scripts/dashboard.py` showing: instrument_id, last_snapshot_time, mark_price, spread_bps, funding_rate, stale status
- **D-09:** Add FeatureStore section showing `sample_count` per instrument — proves signals agent is consuming data for all 5 instruments
- **D-10:** Dashboard follows existing auto-refresh pattern (no one-shot mode)

### Instrument ID correctness
- **D-11:** Runtime assertion in `on_ws_update()` that validates `instrument_id` matches `states[instrument_id].instrument_id` before calling `build_snapshot()`
- **D-12:** Runtime assertion in `build_snapshot()` that validates `state.instrument_id` equals the instrument parameter passed by the caller
- **D-13:** Both assertions are lightweight (dict key lookup comparison) — no production performance concern
- **D-14:** Integration tests specifically verify no snapshot carries a mismatched instrument ID when processing data for all 5 instruments concurrently

### Claude's Discretion
- Test fixture design (shared helpers vs per-test setup)
- Dashboard layout and formatting details
- Assertion error message wording
- Whether assertions use `assert` statements or raise custom exceptions

</decisions>

<specifics>
## Specific Ideas

- Dashboard should show both ME2E-01 evidence (snapshots published per instrument) and ME2E-02 evidence (FeatureStore samples per instrument) in a single view
- The runtime assertions are "belt and suspenders" — the architecture makes ID corruption structurally unlikely, but the checks are cheap insurance

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Ingestion pipeline (test targets)
- `agents/ingestion/main.py` — `on_ws_update()` callback, per-instrument state dict, snapshot publishing with throttle
- `agents/ingestion/normalizer.py` — `build_snapshot()` that constructs MarketSnapshot from state
- `agents/ingestion/state.py` — `IngestionState` with readiness flags (`is_ready()`, `has_ws_tick`, `has_candles`, `has_funding`)

### Signals consumption (test targets)
- `agents/signals/main.py` — Snapshot routing to per-instrument FeatureStores, strategy execution loop
- `agents/signals/feature_store.py` — `FeatureStore.update()` and `sample_count` property

### Existing test infrastructure
- `agents/ingestion/tests/test_main_wiring.py` — Multi-instrument wiring tests (staleness, error isolation) — extend this pattern
- `agents/signals/tests/test_main.py` — Signal agent serialization tests — extend for multi-instrument routing
- `agents/signals/tests/test_feature_store.py` — FeatureStore unit tests with `_snap()` fixture helper

### Configuration
- `configs/default.yaml` — Instrument list (all 5 instruments)
- `configs/strategy_matrix.yaml` — Per-instrument strategy enablement
- `libs/common/instruments.py` — `get_all_instruments()`, `InstrumentConfig`

### Dashboard
- `scripts/dashboard.py` — Existing dashboard with auto-refresh pattern

### Prior phase context
- `.planning/phases/07-websocket-multi-instrument/07-CONTEXT.md` — WS dispatch, readiness gating, staleness detection decisions
- `.planning/phases/08-rest-polling-multi-instrument/08-CONTEXT.md` — REST polling, error isolation, staleness decisions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `test_main_wiring.py` — Already has 6 multi-instrument wiring tests; extend with E2E flow tests
- `_snap()` fixture in `test_feature_store.py` — Creates minimal MarketSnapshot for testing; reuse for signals consumption tests
- `snapshot_to_dict()` / `deserialize_snapshot()` — Existing serialization roundtrip in `signals/main.py`; tests can verify the full serialize→deserialize→route flow

### Established Patterns
- `states: dict[str, IngestionState]` — Per-instrument state dict created at startup; test fixtures can populate this directly
- `IngestionState.is_ready()` — Boolean gate that tests can control by setting readiness flags
- Dashboard uses `structlog` for output and reads Redis stream entries via `RedisConsumer`

### Integration Points
- `on_ws_update(instrument_id)` — Entry point for ingestion E2E tests; call with different instrument IDs and verify snapshots
- FeatureStore dict in `signals/main.py` — Test can create stores for all 5 instruments, feed snapshots, verify `sample_count > 0`
- `dashboard.py` needs to read from Redis streams that contain per-instrument snapshot data

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 09-end-to-end-verification*
*Context gathered: 2026-03-23*
