# Phase 7: WebSocket Multi-Instrument - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

A single WebSocket connection receives real-time market data for all 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY) and routes messages to the correct per-instrument IngestionState. Snapshots are published per-instrument with throttling and data-readiness gating.

</domain>

<decisions>
## Implementation Decisions

### Message dispatch model
- **D-01:** Pass full `states: dict[str, IngestionState]` into `run_ws_market_data()` — extract product ID from each message, look up the correct state, and call `parse_market_data()` with that state directly (no per-message filtering)
- **D-02:** Product ID → instrument ID mapping (`ETH-PERP-INTX` → `ETH-PERP`) via a simple dict built at startup from `InstrumentConfig` objects — no registry method needed
- **D-03:** `on_update` callback signature changes to `on_update(instrument_id: str)` so the caller knows which instrument was updated
- **D-04:** Messages with unrecognized product IDs are logged as a warning and dropped

### Per-instrument snapshot publishing
- **D-05:** Each instrument's WS update independently triggers a snapshot publish, throttled to at most 1 snapshot per instrument per 100ms
- **D-06:** All snapshots go to the single `stream:market_snapshots` channel (signals agent already routes by `snapshot.instrument`)
- **D-07:** Snapshot logging uses a single global counter (log every 100th snapshot), with instrument ID included in the log line

### Partial-data handling at startup
- **D-08:** Don't publish snapshots for an instrument until it has received at least one candle update, one funding rate update, AND WS price data — simple boolean flags (`has_candles`, `has_funding`, `has_ws_tick`) per instrument, flipped on first data arrival
- **D-09:** Only publish snapshots for instruments that have all data sources active — after Phase 7 (before Phase 8), only ETH-PERP will have REST data, so only ETH-PERP publishes snapshots; other instruments wait for Phase 8
- **D-10:** On reconnect, if an instrument's WS data doesn't arrive within 30 seconds (reuse `STALE_DATA_HALT_SECONDS`), mark that instrument's state as stale
- **D-11:** Log `"instrument_ws_ready"` per instrument on first WS data arrival for startup verification

### Claude's Discretion
- Throttle implementation (asyncio timer vs timestamp check)
- Where to place the ws_product_id→instrument_id mapping dict
- How to structure the per-instrument readiness flags (on IngestionState or separate tracker)
- Test fixture design for multi-instrument WS message routing

</decisions>

<specifics>
## Specific Ideas

No specific requirements — the Phase 6 scaffolding (InstrumentConfig registry, per-instrument IngestionState dict, ws_product_id property) already defines the integration surface.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### WebSocket data flow
- `agents/ingestion/sources/ws_market_data.py` — Current single-instrument WS handler; `run_ws_market_data()` and `parse_market_data()` are the two functions to refactor
- `libs/coinbase/ws_client.py` — WS client with `subscribe(product_ids=...)` that already accepts multiple product IDs; reconnect logic replays all subscriptions automatically

### Ingestion orchestration
- `agents/ingestion/main.py` — Current wiring: single-instrument WS task, `on_ws_update()` callback, TaskGroup structure
- `agents/ingestion/state.py` — `IngestionState` dataclass, already has `instrument_id` field from Phase 6

### Instrument config
- `libs/common/instruments.py` — `InstrumentConfig` with `ws_product_id` property, `get_all_instruments()` for startup enumeration
- `libs/common/constants.py` — `STALE_DATA_HALT_SECONDS = 30` for staleness threshold reuse

### Phase 6 context
- `.planning/phases/06-config-state-foundation/06-CONTEXT.md` — Prior decisions on config structure, WS product ID convention (`{id}-INTX`), per-instrument state lifecycle

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `CoinbaseWSClient.subscribe(product_ids=[...])` — already supports multi-product subscription in a single call
- `CoinbaseWSClient.listen()` — reconnect logic replays `_subscriptions` list, so multi-product resubscribe works automatically
- `parse_market_data()` — already parameterized with `ws_product_id`, filters per product; refactor to dispatch model is straightforward
- `InstrumentConfig.ws_product_id` property — derives WS product IDs from instrument IDs at startup

### Established Patterns
- `states: dict[str, IngestionState]` already created in `main.py` — the dispatch target exists
- `build_snapshot(state)` already returns `None` when critical fields are missing — the gating pattern extends naturally with readiness flags
- Log pattern: `logger.info("event_name", instrument=..., key=value)` — instrument field already used across codebase

### Integration Points
- `run_ws_market_data()` — must change from single-state to multi-state, single-product to multi-product
- `on_ws_update()` in `main.py` — must accept `instrument_id` parameter, look up correct state, build that instrument's snapshot
- `build_snapshot()` — needs readiness check before building (has_candles, has_funding, has_ws_tick)
- Throttle layer sits between WS update and snapshot publish in `on_ws_update()`

</code_context>

<deferred>
## Deferred Ideas

- Per-instrument WS connections for isolation — unnecessary, single connection with multi-product subscription is the Coinbase recommended pattern
- WS message batching/aggregation across instruments — adds latency for no benefit given 100ms throttle

</deferred>

---

*Phase: 07-websocket-multi-instrument*
*Context gathered: 2026-03-22*
