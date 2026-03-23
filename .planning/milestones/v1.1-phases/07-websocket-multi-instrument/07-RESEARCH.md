# Phase 7: WebSocket Multi-Instrument - Research

**Researched:** 2026-03-22
**Domain:** WebSocket multi-product subscription, per-instrument message dispatch, snapshot throttling
**Confidence:** HIGH

## Summary

Phase 7 refactors the ingestion agent's WebSocket path from single-instrument to multi-instrument. The existing infrastructure is well-prepared: `CoinbaseWSClient.subscribe()` already accepts multiple product IDs, `parse_market_data()` already filters by `ws_product_id`, `states: dict[str, IngestionState]` already exists, and `InstrumentConfig.ws_product_id` derives the WS product ID from the instrument ID. The work is primarily a wiring change in `ws_market_data.py` and `main.py`, plus adding per-instrument readiness flags and snapshot throttling.

The Coinbase Advanced Trade WebSocket sends all subscribed products' data over a single connection. Messages include `product_id` fields in ticker events (inside each ticker object), trade events (inside each trade object), and L2 events (at the event level). The existing `parse_market_data()` already filters by product ID -- the refactor changes it from filtering to dispatching across multiple states.

**Primary recommendation:** Refactor `run_ws_market_data()` to accept `states: dict[str, IngestionState]` and a product-to-instrument mapping dict, subscribe to all product IDs at once, and dispatch each message to the correct state. Add readiness flags to `IngestionState` and per-instrument throttle tracking in `on_ws_update()`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Pass full `states: dict[str, IngestionState]` into `run_ws_market_data()` -- extract product ID from each message, look up the correct state, and call `parse_market_data()` with that state directly (no per-message filtering)
- **D-02:** Product ID to instrument ID mapping (`ETH-PERP-INTX` -> `ETH-PERP`) via a simple dict built at startup from `InstrumentConfig` objects -- no registry method needed
- **D-03:** `on_update` callback signature changes to `on_update(instrument_id: str)` so the caller knows which instrument was updated
- **D-04:** Messages with unrecognized product IDs are logged as a warning and dropped
- **D-05:** Each instrument's WS update independently triggers a snapshot publish, throttled to at most 1 snapshot per instrument per 100ms
- **D-06:** All snapshots go to the single `stream:market_snapshots` channel (signals agent already routes by `snapshot.instrument`)
- **D-07:** Snapshot logging uses a single global counter (log every 100th snapshot), with instrument ID included in the log line
- **D-08:** Don't publish snapshots for an instrument until it has received at least one candle update, one funding rate update, AND WS price data -- simple boolean flags (`has_candles`, `has_funding`, `has_ws_tick`) per instrument, flipped on first data arrival
- **D-09:** Only publish snapshots for instruments that have all data sources active -- after Phase 7 (before Phase 8), only ETH-PERP will have REST data, so only ETH-PERP publishes snapshots; other instruments wait for Phase 8
- **D-10:** On reconnect, if an instrument's WS data doesn't arrive within 30 seconds (reuse `STALE_DATA_HALT_SECONDS`), mark that instrument's state as stale
- **D-11:** Log `"instrument_ws_ready"` per instrument on first WS data arrival for startup verification

### Claude's Discretion
- Throttle implementation (asyncio timer vs timestamp check)
- Where to place the ws_product_id to instrument_id mapping dict
- How to structure the per-instrument readiness flags (on IngestionState or separate tracker)
- Test fixture design for multi-instrument WS message routing

### Deferred Ideas (OUT OF SCOPE)
- Per-instrument WS connections for isolation -- unnecessary, single connection with multi-product subscription is the Coinbase recommended pattern
- WS message batching/aggregation across instruments -- adds latency for no benefit given 100ms throttle
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MWS-01 | WebSocket client subscribes to all active instruments via single connection with multi-product subscription | `CoinbaseWSClient.subscribe()` already accepts `product_ids: list[str]`; build list from `get_all_instruments()` at startup |
| MWS-02 | Incoming WS messages are routed to the correct per-instrument IngestionState by product ID | Product ID extraction from message events + dict lookup dispatch; existing `parse_market_data()` already parameterized with `ws_product_id` |
</phase_requirements>

## Standard Stack

No new libraries needed. This phase uses only existing project dependencies.

### Core (existing, unchanged)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| websockets | 13+ | WebSocket connection (via `CoinbaseWSClient`) | Already in use |
| orjson | 3.9+ | Message deserialization in WS client | Already in use |
| asyncio | stdlib | Event loop, TaskGroup | Already in use |

## Architecture Patterns

### Current Structure (single-instrument)
```
main.py
  -> run_ws_market_data(ws_client, states["ETH-PERP"], on_update, ws_product_id)
       -> subscribe([ws_product_id])
       -> for message: parse_market_data(message, state, ws_product_id)
       -> on_update()   # no instrument context
```

### Target Structure (multi-instrument)
```
main.py
  -> run_ws_market_data(ws_client, states, product_to_instrument, on_update)
       -> subscribe(all_product_ids)
       -> for message: extract product_id, lookup instrument, dispatch
       -> parse_market_data(message, states[instrument_id], product_id)
       -> on_update(instrument_id)
```

### Pattern 1: Product ID Extraction from WS Messages

**What:** Coinbase Advanced Trade WS messages embed product_id at different levels depending on channel type. The dispatch layer must extract it before calling `parse_market_data()`.

**Channel-specific product ID locations:**
```python
# ticker: product_id is inside each ticker object within events[].tickers[]
{"channel": "ticker", "events": [{"tickers": [{"product_id": "ETH-PERP-INTX", ...}]}]}

# market_trades: product_id is inside each trade object within events[].trades[]
{"channel": "market_trades", "events": [{"trades": [{"product_id": "ETH-PERP-INTX", ...}]}]}

# l2_data: product_id is at the event level within events[]
{"channel": "l2_data", "events": [{"product_id": "ETH-PERP-INTX", "updates": [...]}]}
```

**Key insight:** A single WS message can contain events for multiple products (especially ticker batches). The dispatch must handle this -- but since `parse_market_data()` already filters by `ws_product_id` internally, the simplest approach is to call it once per state with the full message, letting the internal filtering handle it. However, this is O(instruments * events) per message. The more efficient approach per D-01 is to extract the product ID from the message first and dispatch to the single correct state.

**Recommended approach:** For ticker and market_trades, events can contain multiple products. Rather than trying to extract a single product ID from the top-level message, iterate events and group by product_id, then dispatch. For l2_data, product_id is at the event level, so extraction is direct.

**Simpler alternative (recommended):** Since `parse_market_data()` already filters by product_id internally, and the number of instruments is small (5), call `parse_market_data()` for each instrument's state with its product_id. This is the least-invasive change.

**But D-01 says** "extract product ID from each message, look up the correct state, and call `parse_market_data()` with that state directly (no per-message filtering)." This means we need a dispatch function that extracts product IDs from events and routes to the right state.

### Pattern 2: Readiness Flags on IngestionState

**What:** Per-instrument boolean flags tracking whether each data source has delivered at least one update.

**Recommendation: Add flags directly to IngestionState** (it already has `last_ws_update`, `last_funding_update` fields that serve a similar purpose). This is the natural home.

```python
# Add to IngestionState dataclass:
has_ws_tick: bool = False
has_candles: bool = False
has_funding: bool = False

def is_ready(self) -> bool:
    """All data sources have delivered at least one update."""
    return self.has_ws_tick and self.has_candles and self.has_funding
```

**Where flags get set:**
- `has_ws_tick = True` -- in `parse_market_data()` when first WS update arrives (or in dispatch layer)
- `has_candles = True` -- in `run_all_candle_pollers()` after first successful candle fetch
- `has_funding = True` -- in `run_funding_poller()` after first successful funding fetch

### Pattern 3: Per-Instrument Snapshot Throttle

**What:** Limit snapshot publishing to at most 1 per instrument per 100ms. Two approaches:

**Option A: Timestamp check (recommended)**
```python
# In on_ws_update() in main.py
_last_publish: dict[str, float] = {}  # instrument_id -> monotonic time

async def on_ws_update(instrument_id: str) -> None:
    now = time.monotonic()
    last = _last_publish.get(instrument_id, 0.0)
    if now - last < 0.1:  # 100ms throttle
        return
    _last_publish[instrument_id] = now
    # ... build and publish snapshot
```

**Why timestamp over asyncio timer:** Simpler, no timer management, no edge cases with timer cancellation. A timestamp check is deterministic and testable (inject a clock). This is the standard pattern in this codebase (see `STALE_DATA_HALT_SECONDS` which also uses time comparison).

**Option B: asyncio timer** -- Creates a timer per instrument that fires at most every 100ms. More complex, requires tracking timer handles, but ensures the last update within a window always publishes. For 100ms granularity on trading data, timestamp check is sufficient.

**Recommendation:** Timestamp check (Option A).

### Pattern 4: Product-to-Instrument Mapping Dict

**What:** Simple dict built at startup from `InstrumentConfig` objects.

```python
# Build at startup in main.py
product_to_instrument: dict[str, str] = {
    inst.ws_product_id: inst.id for inst in get_all_instruments()
}
# Result: {"ETH-PERP-INTX": "ETH-PERP", "BTC-PERP-INTX": "BTC-PERP", ...}
```

**Where to place it:** Build in `main.py` and pass to `run_ws_market_data()`. No need for a module-level or registry method -- it's a simple startup computation.

### Anti-Patterns to Avoid
- **One WS connection per instrument:** Coinbase recommends single connection with multi-product subscription. Multiple connections waste resources and may hit connection limits.
- **Blocking dispatch:** Never do synchronous work in the message dispatch loop. `parse_market_data()` is CPU-only (Decimal parsing), which is fine on asyncio since it's fast.
- **Global state for throttle:** Keep throttle state local to the callback scope, not module-level.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WS reconnect | Custom reconnect loop | `CoinbaseWSClient.listen()` | Already handles reconnect with backoff and re-subscribes all channels |
| Multi-product subscribe | Per-instrument subscribe calls | Single `subscribe(product_ids=[...all...])` | One subscribe message per channel covering all products |
| Staleness detection | Custom timer per instrument | `STALE_DATA_HALT_SECONDS` constant + `last_ws_update` | Already exists in IngestionState |

**Key insight:** The existing `CoinbaseWSClient` handles the hard parts (reconnect, re-subscribe). Phase 7 is about dispatch logic and readiness gating, not connection management.

## Common Pitfalls

### Pitfall 1: Mixed Product IDs in Single WS Message
**What goes wrong:** Assuming each WS message contains data for exactly one product. Ticker messages from Coinbase can batch multiple products in a single message.
**Why it happens:** Single-instrument code never encountered multi-product messages.
**How to avoid:** The dispatch function must iterate events within a message and handle per-event product IDs, not assume a single product per message.
**Warning signs:** State updates going to wrong instrument; tests only use single-product messages.

### Pitfall 2: Reconnect Doesn't Re-subscribe All Products
**What goes wrong:** After reconnect, only some instruments receive data.
**Why it happens:** If subscribe is called per-product instead of storing all product IDs in `_subscriptions`.
**How to avoid:** Already handled -- `CoinbaseWSClient.connect()` replays all `_subscriptions` on reconnect. The single `subscribe(product_ids=[all])` call stores all product IDs in the subscription list.
**Warning signs:** After network blip, only first instrument gets updates.

### Pitfall 3: Readiness Flags Not Set by REST Pollers
**What goes wrong:** Snapshot publishing is gated by `has_candles` and `has_funding`, but the REST pollers (Phase 8 for non-ETH instruments) don't set these flags.
**Why it happens:** REST pollers were written before readiness flags existed.
**How to avoid:** D-09 explicitly addresses this -- only ETH-PERP has REST data until Phase 8, so only ETH-PERP will pass readiness. The candle and funding pollers must set `state.has_candles = True` / `state.has_funding = True` on first data. This needs to be added to the existing ETH-PERP pollers too.
**Warning signs:** No snapshots published despite WS data flowing.

### Pitfall 4: Throttle Using wall-clock time
**What goes wrong:** Using `datetime.now()` or `utc_now()` for throttle comparison introduces system clock jitter sensitivity.
**How to avoid:** Use `time.monotonic()` for throttle intervals -- immune to NTP jumps.
**Warning signs:** Throttle behaving erratically during time adjustments.

### Pitfall 5: Product ID Extraction Differs by Channel
**What goes wrong:** Using one extraction path for all channels. Ticker has `events[].tickers[].product_id`, L2 has `events[].product_id`, trades has `events[].trades[].product_id`.
**Why it happens:** Not reading the Coinbase WS message format carefully for each channel.
**How to avoid:** The existing `parse_market_data()` already handles this correctly per-channel. If refactoring dispatch to extract product_id before calling parse, need three extraction paths.
**Recommendation:** Keep `parse_market_data()` as the per-instrument dispatcher -- it already handles channel-specific product ID filtering. The outer dispatch in `run_ws_market_data()` can extract a "primary" product_id for routing, but `parse_market_data()` still does the authoritative filtering.

## Code Examples

### Multi-Instrument WS Runner (target shape)
```python
# Source: refactored agents/ingestion/sources/ws_market_data.py
async def run_ws_market_data(
    ws_client: CoinbaseWSClient,
    states: dict[str, IngestionState],
    product_to_instrument: dict[str, str],
    on_update: Callable[[str], Any] | None = None,
) -> None:
    product_ids = list(product_to_instrument.keys())
    await ws_client.subscribe(
        channels=["ticker", "level2", "market_trades"],
        product_ids=product_ids,
    )

    async for message in ws_client.listen():
        instrument_ids = _dispatch_message(message, states, product_to_instrument)
        if on_update is not None:
            for instrument_id in instrument_ids:
                await on_update(instrument_id)
```

### Message Dispatch (extracting product IDs)
```python
# Source: new function in ws_market_data.py
def _dispatch_message(
    message: dict[str, Any],
    states: dict[str, IngestionState],
    product_to_instrument: dict[str, str],
) -> list[str]:
    """Dispatch a WS message to the correct per-instrument states.

    Returns list of instrument_ids that were updated.
    """
    channel = message.get("channel", "")
    if channel in ("", "subscriptions", "heartbeats"):
        return []

    # Extract all product IDs mentioned in this message
    product_ids = _extract_product_ids(message, channel)

    updated_instruments: list[str] = []
    for product_id in product_ids:
        instrument_id = product_to_instrument.get(product_id)
        if instrument_id is None:
            logger.warning("unrecognized_product_id", product_id=product_id)
            continue
        state = states.get(instrument_id)
        if state is None:
            continue
        if parse_market_data(message, state, product_id):
            if not state.has_ws_tick:
                state.has_ws_tick = True
                logger.info("instrument_ws_ready", instrument=instrument_id)
            updated_instruments.append(instrument_id)

    return updated_instruments
```

### Readiness-Gated Snapshot Publishing
```python
# Source: refactored on_ws_update in main.py
import time

_last_publish: dict[str, float] = {}
_THROTTLE_SECONDS = 0.1  # 100ms

async def on_ws_update(instrument_id: str) -> None:
    nonlocal snapshot_count
    # Throttle: at most 1 snapshot per instrument per 100ms
    now = time.monotonic()
    if now - _last_publish.get(instrument_id, 0.0) < _THROTTLE_SECONDS:
        return
    _last_publish[instrument_id] = now

    state = states[instrument_id]
    # Readiness gate: all data sources must have delivered
    if not state.is_ready():
        return

    snapshot = build_snapshot(state)
    if snapshot is None:
        return

    payload = snapshot_to_dict(snapshot)
    await publisher.publish(Channel.MARKET_SNAPSHOTS, payload)

    snapshot_count += 1
    if snapshot_count % 100 == 1:
        logger.info(
            "snapshot_published",
            instrument=instrument_id,
            count=snapshot_count,
            mark_price=str(snapshot.mark_price),
        )
```

### Product-to-Instrument Mapping
```python
# Source: built in main.py at startup
instruments = get_all_instruments()
product_to_instrument: dict[str, str] = {
    inst.ws_product_id: inst.id for inst in instruments
}
# {"ETH-PERP-INTX": "ETH-PERP", "BTC-PERP-INTX": "BTC-PERP", ...}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single `IngestionState` passed to WS handler | `dict[str, IngestionState]` keyed by instrument ID | Phase 6 | Foundation for multi-instrument dispatch |
| Hardcoded `"ETH-PERP-INTX"` product ID | `InstrumentConfig.ws_product_id` property | Phase 6 | Dynamic product ID derivation |
| Single-instrument `on_update()` | Must become `on_update(instrument_id: str)` | Phase 7 | Enables per-instrument snapshot publishing |

## Open Questions

1. **Product ID extraction for dispatch vs. letting parse_market_data filter**
   - What we know: D-01 says "extract product ID from each message, look up the correct state" -- this requires pre-extraction
   - What's unclear: Whether to extract a single "primary" product_id per message or iterate all events to find all product_ids
   - Recommendation: Extract all unique product_ids from events, then call `parse_market_data()` once per product_id with the correct state. This is faithful to D-01 and handles multi-product messages.

2. **Stale instrument detection on reconnect (D-10)**
   - What we know: After reconnect, check if each instrument receives data within 30s
   - What's unclear: Whether to add a new field to IngestionState or reuse `last_ws_update` with a periodic checker
   - Recommendation: Reuse `last_ws_update` -- after reconnect, a background timer checks each instrument's `last_ws_update` against `STALE_DATA_HALT_SECONDS`. No new field needed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio 0.23+ |
| Config file | `pyproject.toml` (existing) |
| Quick run command | `python -m pytest agents/ingestion/tests/ -x -q` |
| Full suite command | `python -m pytest agents/ingestion/tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MWS-01 | WS subscribes to all active instrument product IDs | unit | `python -m pytest agents/ingestion/tests/test_ws_market_data.py::TestMultiInstrumentSubscribe -x` | No -- Wave 0 |
| MWS-02 | Messages routed to correct per-instrument state | unit | `python -m pytest agents/ingestion/tests/test_ws_market_data.py::TestMultiInstrumentDispatch -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest agents/ingestion/tests/ -x -q`
- **Per wave merge:** `python -m pytest agents/ingestion/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/ingestion/tests/test_ws_market_data.py` -- needs new test classes for multi-instrument dispatch, unrecognized product ID warning, readiness gating
- [ ] `agents/ingestion/tests/test_normalizer.py` -- may need tests for readiness flag interaction with `build_snapshot()`
- [ ] No new framework install needed -- pytest infrastructure exists

## Sources

### Primary (HIGH confidence)
- `agents/ingestion/sources/ws_market_data.py` -- current single-instrument WS handler (read directly)
- `libs/coinbase/ws_client.py` -- WS client with multi-product subscribe support (read directly)
- `agents/ingestion/main.py` -- current wiring and TaskGroup structure (read directly)
- `agents/ingestion/state.py` -- IngestionState dataclass (read directly)
- `libs/common/instruments.py` -- InstrumentConfig with ws_product_id (read directly)
- `agents/ingestion/tests/test_ws_market_data.py` -- existing test patterns (read directly)

### Secondary (MEDIUM confidence)
- Coinbase Advanced Trade WS message format -- derived from existing parsing code and docstrings in `ws_market_data.py`

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, purely internal refactoring
- Architecture: HIGH -- all target code read directly, patterns are straightforward wiring changes
- Pitfalls: HIGH -- derived from direct code reading and Coinbase WS message format analysis

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- internal refactoring, no external dependency changes)
