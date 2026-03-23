# Phase 8: REST Polling Multi-Instrument - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Candle and funding rate pollers fetch data for each of the 5 active instruments independently (concurrent polling), producing per-instrument data in the ingestion pipeline. Currently both pollers are hardcoded to ETH-PERP only.

</domain>

<decisions>
## Implementation Decisions

### Concurrency & rate limiting
- **D-01:** Stagger instrument starts at startup (e.g., 2-second delay between instruments) to avoid a 30-request burst against the 10 req/s rate limit
- **D-02:** After initial fetch, instruments poll on the same cadence per timeframe — no per-instrument polling offsets. Natural drift from staggered starts is sufficient.
- **D-03:** Rate-limited polls are silently skipped (existing behavior) — "skip and wait for next interval" is acceptable even with 5x the traffic
- **D-04:** One shared `RateLimiter` instance across all pollers — gives a global view of the rate budget

### Error isolation
- **D-05:** Per-instrument pollers are isolated — if one instrument hits a persistent error, the other 4 keep running. Unexpected exceptions in a poller are caught and logged, NOT propagated to tear down the TaskGroup.
- **D-06:** Log when an instrument's poller has N consecutive failures (e.g., "ETH-PERP candle poller: 5 consecutive failures") — new consecutive-failure counter and warning log
- **D-07:** Add REST data staleness detection similar to WS staleness (D-10 from Phase 7). If an instrument's candle or funding data hasn't been successfully updated within a threshold, mark the data as stale so the readiness gate prevents snapshot publishing with stale REST data.

### REST client architecture
- **D-08:** Per-instrument `CoinbaseRESTClient` instances — each instrument gets its own HTTP connection pool for isolated connection state
- **D-09:** All per-instrument clients share the single `RateLimiter` instance (same API key, same rate limit bucket on Coinbase Advanced)
- **D-10:** No explicit request timeout tuning — leave httpx defaults as-is

### Claude's Discretion
- Exact stagger delay between instrument starts
- Consecutive failure threshold for the warning log
- REST staleness threshold value (can reuse STALE_DATA_HALT_SECONDS or define a REST-specific one)
- How to structure the per-instrument error isolation (try/except wrapper vs separate coroutine)
- Where to instantiate per-instrument REST clients (main.py startup vs factory)

</decisions>

<specifics>
## Specific Ideas

- Naming correction: all references to "Coinbase INTX" in docstrings and comments should be updated to "Coinbase Advanced" — this is the correct exchange name
- The functions `poll_candles_once`, `run_candle_poller`, `run_all_candle_pollers`, `poll_funding_once`, and `run_funding_poller` already accept `instrument_id` as a parameter — the refactor is primarily in `main.py` wiring

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### REST pollers
- `agents/ingestion/sources/candles.py` — `run_all_candle_pollers()` already parameterized with `instrument_id`; needs to be called per-instrument
- `agents/ingestion/sources/funding_rate.py` — `run_funding_poller()` already parameterized with `instrument_id`; needs to be called per-instrument

### Ingestion orchestration
- `agents/ingestion/main.py` — Lines 141-153 hardcode `states["ETH-PERP"]` for both REST pollers; this is the primary refactor target
- `agents/ingestion/state.py` — `IngestionState` with readiness flags (`has_candles`, `has_funding`, `has_ws_tick`, `is_ready()`)

### Instrument config
- `libs/common/instruments.py` — `get_all_instruments()` returns all 5 active instruments for startup enumeration
- `libs/coinbase/rest_client.py` — `CoinbaseRESTClient` constructor and methods; needs per-instrument instantiation
- `libs/coinbase/rate_limiter.py` — `RateLimiter` shared across all clients

### Phase 7 context (staleness pattern to reuse)
- `.planning/phases/07-websocket-multi-instrument/07-CONTEXT.md` — D-10: WS staleness detection pattern (reset readiness flag after threshold)
- `agents/ingestion/sources/ws_market_data.py` — `_mark_stale_instruments()` as reference implementation for REST staleness

### Constants
- `libs/common/constants.py` — `STALE_DATA_HALT_SECONDS = 30` for potential reuse as REST staleness threshold

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `poll_candles_once()` / `run_candle_poller()` / `run_all_candle_pollers()` — already accept `instrument_id`, just need multi-instrument wiring
- `poll_funding_once()` / `run_funding_poller()` — already accept `instrument_id`, same situation
- `get_all_instruments()` — returns list of `InstrumentConfig` objects for startup enumeration
- `_mark_stale_instruments()` in `ws_market_data.py` — staleness detection pattern to adapt for REST data

### Established Patterns
- `states: dict[str, IngestionState]` in `main.py` — per-instrument state dict already exists
- `IngestionState.is_ready()` — readiness gate checks `has_candles`, `has_funding`, `has_ws_tick`
- Error handling: catch specific exceptions first (`RateLimitExceededError`, `CoinbaseAPIError`), then generic `Exception`

### Integration Points
- `main.py` TaskGroup — currently spawns one candle task and one funding task; needs to spawn per-instrument tasks with staggered starts
- `CoinbaseRESTClient` constructor — needs to be called per-instrument, sharing a single `RateLimiter`
- `IngestionState` readiness flags — REST staleness detection should reset `has_candles`/`has_funding` similar to how WS staleness resets `has_ws_tick`

</code_context>

<deferred>
## Deferred Ideas

- Per-instrument rate limiting (separate rate budgets) — REQUIREMENTS.md explicitly marks this out of scope: "Use existing rate limiter — monitor for issues before adding complexity"
- Adaptive polling intervals based on market activity — future optimization, not needed for multi-instrument

</deferred>

---

*Phase: 08-rest-polling-multi-instrument*
*Context gathered: 2026-03-22*
