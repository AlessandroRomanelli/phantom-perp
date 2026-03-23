---
phase: 07-websocket-multi-instrument
verified: 2026-03-22T18:05:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 07: WebSocket Multi-Instrument Verification Report

**Phase Goal:** Refactor WebSocket ingestion from single-instrument to multi-instrument — one connection subscribes to all 5 perpetual contracts, per-instrument dispatch, readiness gating, and staleness detection.
**Verified:** 2026-03-22T18:05:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                                                                   | Status     | Evidence                                                                                                               |
| --- | ----------------------------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| 1   | WebSocket subscribes to all 5 instrument product IDs in a single subscribe call                                         | VERIFIED   | `run_ws_market_data` calls `ws_client.subscribe(product_ids=list(product_to_instrument.keys()))` — ws_market_data.py:356-359 |
| 2   | Incoming WS messages are dispatched to the correct per-instrument IngestionState by product ID                          | VERIFIED   | `_dispatch_message` maps product_id → instrument_id via `product_to_instrument`, calls `states.get(instrument_id)` — ws_market_data.py:109-125 |
| 3   | Messages with unrecognized product IDs are logged as warning and dropped                                                | VERIFIED   | `logger.warning("unrecognized_product_id", product_id=product_id)` then `continue` — ws_market_data.py:112-113         |
| 4   | Snapshots are not published for instruments missing candle, funding, or WS data                                         | VERIFIED   | `if not state.is_ready(): return` in `on_ws_update` — main.py:106-107; `is_ready()` returns `has_ws_tick and has_candles and has_funding` — state.py:79 |
| 5   | Per-instrument snapshot publishing is throttled to at most 1 per 100ms                                                  | VERIFIED   | `_THROTTLE_SECONDS = 0.1`, `time.monotonic()` check per `instrument_id` before publish — main.py:88, 95-98            |
| 6   | on_ws_update receives instrument_id so caller knows which instrument was updated                                        | VERIFIED   | `async def on_ws_update(instrument_id: str)` — main.py:90; `await on_update(instrument_id)` in loop — ws_market_data.py:367-368 |
| 7   | After WS reconnect, instruments that do not receive data within STALE_DATA_HALT_SECONDS are marked stale (has_ws_tick reset to False) | VERIFIED   | Periodic check every `STALE_DATA_HALT_SECONDS` in listen loop calls `_mark_stale_instruments(states)` which resets `has_ws_tick` — ws_market_data.py:370-374; `_mark_stale_instruments` at lines 130-159 |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact                                               | Expected                                                    | Status     | Details                                                                                      |
| ------------------------------------------------------ | ----------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------- |
| `agents/ingestion/state.py`                            | Readiness flags and is_ready() method on IngestionState     | VERIFIED   | `has_ws_tick`, `has_candles`, `has_funding` (lines 69-71); `def is_ready(self) -> bool` (line 73) |
| `agents/ingestion/sources/ws_market_data.py`           | Multi-instrument WS dispatch with _dispatch_message and _extract_product_ids | VERIFIED   | `_dispatch_message` (line 79), `_extract_product_ids` (line 42), `_mark_stale_instruments` (line 130), new `run_ws_market_data` signature (line 336) |
| `agents/ingestion/main.py`                             | Per-instrument throttled snapshot publishing with readiness gating | VERIFIED   | `product_to_instrument` (line 54), `on_ws_update(instrument_id)` (line 90), throttle (lines 87-98), `is_ready()` gate (line 106) |
| `agents/ingestion/tests/test_ws_market_data.py`        | Tests for multi-instrument subscribe, dispatch, unrecognized product, readiness, reconnect staleness | VERIFIED   | `TestMultiInstrumentDispatch` (line 307), `TestMultiInstrumentSubscribe` (line 539), `TestReconnectStaleness` (line 604), `TestReadinessFlags` (line 270) |
| `agents/ingestion/sources/candles.py`                  | Sets has_candles=True on first successful poll              | VERIFIED   | `if not state.has_candles: state.has_candles = True` — candles.py:67-68                     |
| `agents/ingestion/sources/funding_rate.py`             | Sets has_funding=True on first successful poll              | VERIFIED   | `if not state.has_funding: state.has_funding = True` — funding_rate.py:52-53                |

### Key Link Verification

| From                              | To                             | Via                                                                      | Status   | Details                                                                                 |
| --------------------------------- | ------------------------------ | ------------------------------------------------------------------------ | -------- | --------------------------------------------------------------------------------------- |
| `agents/ingestion/main.py`        | `agents/ingestion/sources/ws_market_data.py` | `run_ws_market_data(ws_client, states, product_to_instrument, on_update=on_ws_update)` | WIRED    | Imported at line 36, called at lines 134-139 with `states` and `product_to_instrument` arguments |
| `agents/ingestion/sources/ws_market_data.py` | `agents/ingestion/state.py` | `_dispatch_message` looks up `states.get(instrument_id)` and calls `parse_market_data` | WIRED    | `states.get(instrument_id)` at line 115; `parse_market_data(message, state, product_id)` at line 119 |
| `agents/ingestion/main.py`        | `agents/ingestion/state.py`    | `on_ws_update` checks `state.is_ready()` before publishing snapshot      | WIRED    | `state = states[instrument_id]` at line 100; `if not state.is_ready(): return` at line 106 |
| `agents/ingestion/sources/ws_market_data.py` | `agents/ingestion/state.py` | After reconnect, `_mark_stale_instruments` resets `has_ws_tick` for instruments not updated within `STALE_DATA_HALT_SECONDS` | WIRED    | Imported at line 23; periodic call in listen loop at lines 371-374 |

### Requirements Coverage

| Requirement | Source Plan | Description                                                                                         | Status     | Evidence                                                                                   |
| ----------- | ----------- | --------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| MWS-01      | 07-01-PLAN  | WebSocket client subscribes to all active instruments via single connection with multi-product subscription | SATISFIED  | `ws_client.subscribe(channels=[...], product_ids=list(product_to_instrument.keys()))` — single call subscribing all product IDs; `test_subscribe_all_products` verifies |
| MWS-02      | 07-01-PLAN  | Incoming WS messages are routed to the correct per-instrument IngestionState by product ID          | SATISFIED  | `_dispatch_message` extracts product IDs, maps to instrument IDs, updates correct state; 10 dispatch tests verify routing correctness |

### Anti-Patterns Found

None. Scanned all 6 modified files (`state.py`, `ws_market_data.py`, `main.py`, `candles.py`, `funding_rate.py`, `test_ws_market_data.py`) for TODO/FIXME/placeholder patterns — none found.

### Human Verification Required

None. All behaviors covered by the 32 passing automated tests. No external service integration, UI, or real-time behavior that requires manual observation for this refactor.

### Test Results

- `agents/ingestion/tests/test_ws_market_data.py`: **32/32 passed** (0.13s)
  - `TestParseMarketData`: 11 tests (no regression)
  - `TestReadinessFlags`: 5 tests
  - `TestMultiInstrumentDispatch`: 10 tests
  - `TestMultiInstrumentSubscribe`: 2 tests
  - `TestReconnectStaleness`: 4 tests
- `agents/ingestion/tests/` (full suite): **78/78 passed** (0.21s)

### Implementation Notes

One key_link pattern in the PLAN frontmatter (`states\[instrument_id\]`) does not match the actual implementation (`states.get(instrument_id)` at ws_market_data.py:115). The implementation is functionally correct and defensively superior — `.get()` avoids a `KeyError` if an instrument is not in the states dict. The semantic intent of the link is fully satisfied.

---

_Verified: 2026-03-22T18:05:00Z_
_Verifier: Claude (gsd-verifier)_
