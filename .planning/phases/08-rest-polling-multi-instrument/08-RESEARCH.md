# Phase 8: REST Polling Multi-Instrument - Research

**Researched:** 2026-03-22
**Domain:** Async Python concurrency, REST polling orchestration, error isolation
**Confidence:** HIGH

## Summary

Phase 8 is a focused wiring refactor in `agents/ingestion/main.py` to call existing parameterized candle and funding rate pollers for all 5 instruments instead of only ETH-PERP. The poller functions (`run_all_candle_pollers`, `run_funding_poller`) already accept `instrument_id` and `state` parameters -- the work is primarily in main.py task spawning, per-instrument REST client instantiation, staggered startup, error isolation, and REST data staleness detection.

The codebase is well-prepared: `states: dict[str, IngestionState]` already exists per-instrument, `get_all_instruments()` provides the instrument list, and the WS staleness pattern from Phase 7 (`_mark_stale_instruments` in `ws_market_data.py`) provides a template for REST staleness.

**Primary recommendation:** Refactor main.py to loop over `get_all_instruments()`, create per-instrument `CoinbaseRESTClient` instances sharing one `RateLimiter`, spawn staggered per-instrument candle and funding tasks with error-isolating wrappers, and add a REST staleness checker analogous to the WS pattern.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Stagger instrument starts at startup (e.g., 2-second delay between instruments) to avoid a 30-request burst against the 10 req/s rate limit
- **D-02:** After initial fetch, instruments poll on the same cadence per timeframe -- no per-instrument polling offsets. Natural drift from staggered starts is sufficient.
- **D-03:** Rate-limited polls are silently skipped (existing behavior) -- "skip and wait for next interval" is acceptable even with 5x the traffic
- **D-04:** One shared `RateLimiter` instance across all pollers -- gives a global view of the rate budget
- **D-05:** Per-instrument pollers are isolated -- if one instrument hits a persistent error, the other 4 keep running. Unexpected exceptions in a poller are caught and logged, NOT propagated to tear down the TaskGroup.
- **D-06:** Log when an instrument's poller has N consecutive failures (e.g., "ETH-PERP candle poller: 5 consecutive failures") -- new consecutive-failure counter and warning log
- **D-07:** Add REST data staleness detection similar to WS staleness (D-10 from Phase 7). If an instrument's candle or funding data hasn't been successfully updated within a threshold, mark the data as stale so the readiness gate prevents snapshot publishing with stale REST data.
- **D-08:** Per-instrument `CoinbaseRESTClient` instances -- each instrument gets its own HTTP connection pool for isolated connection state
- **D-09:** All per-instrument clients share the single `RateLimiter` instance (same API key, same rate limit bucket on Coinbase Advanced)
- **D-10:** No explicit request timeout tuning -- leave httpx defaults as-is

### Claude's Discretion
- Exact stagger delay between instrument starts
- Consecutive failure threshold for the warning log
- REST staleness threshold value (can reuse STALE_DATA_HALT_SECONDS or define a REST-specific one)
- How to structure the per-instrument error isolation (try/except wrapper vs separate coroutine)
- Where to instantiate per-instrument REST clients (main.py startup vs factory)

### Deferred Ideas (OUT OF SCOPE)
- Per-instrument rate limiting (separate rate budgets) -- REQUIREMENTS.md explicitly marks this out of scope
- Adaptive polling intervals based on market activity -- future optimization
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MPOL-01 | Candle poller fetches candles for each active instrument independently (5 instruments x N timeframes concurrent) | `run_all_candle_pollers()` already accepts `instrument_id`; main.py needs to spawn one task per instrument with staggered starts and error isolation |
| MPOL-02 | Funding rate poller fetches funding for each active instrument independently (5 concurrent pollers) | `run_funding_poller()` already accepts `instrument_id`; main.py needs to spawn one task per instrument with staggered starts and error isolation |
</phase_requirements>

## Architecture Patterns

### Current Architecture (ETH-PERP only)
```
main.py TaskGroup:
  Task 1: run_ws_market_data(ws_client, states, ...) -- all instruments via WS
  Task 2: run_all_candle_pollers(rest_client, states["ETH-PERP"], "ETH-PERP") -- 5 timeframe sub-tasks
  Task 3: run_funding_poller(rest_client, states["ETH-PERP"], ..., "ETH-PERP") -- single poller
```

### Target Architecture (all instruments)
```
main.py TaskGroup:
  Task 1: run_ws_market_data(ws_client, states, ...) -- unchanged
  Task 2-6: run_instrument_candle_poller(clients["ETH-PERP"], states["ETH-PERP"], "ETH-PERP")
             run_instrument_candle_poller(clients["BTC-PERP"], states["BTC-PERP"], "BTC-PERP")
             ... (one per instrument, staggered start, error-isolated)
  Task 7-11: run_instrument_funding_poller(clients["ETH-PERP"], states["ETH-PERP"], ..., "ETH-PERP")
              ... (one per instrument, staggered start, error-isolated)
  Task 12: REST staleness checker (periodic, analogous to WS staleness)
```

### Pattern 1: Error-Isolating Wrapper Coroutine
**What:** Wrap each per-instrument poller in an async function that catches all exceptions, logs them, tracks consecutive failures, and never propagates to the TaskGroup.
**When to use:** For all per-instrument REST polling tasks (D-05, D-06).
**Recommendation:** Use a dedicated wrapper coroutine rather than modifying `run_all_candle_pollers` or `run_funding_poller` -- keeps the existing functions clean and testable.

```python
async def _run_isolated(
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    instrument_id: str,
    poller_name: str,
    consecutive_failure_threshold: int = 5,
) -> None:
    """Run a poller coroutine with error isolation and failure tracking."""
    consecutive_failures = 0
    while True:
        try:
            await coro_factory()
            consecutive_failures = 0
            return  # Normal exit (shouldn't happen for infinite pollers)
        except Exception as e:
            consecutive_failures += 1
            logger.error(
                "rest_poller_error",
                instrument=instrument_id,
                poller=poller_name,
                error=str(e),
                consecutive_failures=consecutive_failures,
            )
            if consecutive_failures >= consecutive_failure_threshold:
                logger.warning(
                    "rest_poller_consecutive_failures",
                    instrument=instrument_id,
                    poller=poller_name,
                    count=consecutive_failures,
                )
            await asyncio.sleep(30)  # Back off before retry
```

**Alternative approach:** Since `poll_candles_once` and `poll_funding_once` already catch their own exceptions internally (RateLimitExceededError, CoinbaseAPIError, generic Exception), the wrapper mainly needs to catch unexpected crashes in the outer `run_candle_poller`/`run_funding_poller` loop (e.g., connection pool errors from httpx). The inner functions already handle API-level errors gracefully. The consecutive failure counter should be tracked at the `poll_*_once` level (inside the existing functions or via a callback) rather than at the wrapper level, since the existing error handling swallows exceptions.

**Recommended simpler approach:** Modify `run_candle_poller` and `run_funding_poller` to track consecutive failures internally (they already have try/except), and wrap the top-level call in main.py with a simple never-propagate wrapper:

```python
async def _run_rest_poller_isolated(
    coro: Coroutine[Any, Any, None],
    instrument_id: str,
    poller_name: str,
) -> None:
    """Wrap a REST poller to prevent TaskGroup teardown on unexpected crash."""
    try:
        await coro
    except Exception as e:
        logger.error(
            "rest_poller_crashed",
            instrument=instrument_id,
            poller=poller_name,
            error=str(e),
            exc_type=type(e).__name__,
        )
        # Do NOT re-raise -- other instruments keep running (D-05)
```

### Pattern 2: Staggered Startup
**What:** Delay each instrument's poller start by a fixed offset to spread the initial burst of API calls.
**When to use:** At startup in main.py when spawning per-instrument tasks.
**Recommendation:** 2-second stagger between instruments (5 instruments = 8 seconds total spread).

```python
STAGGER_DELAY_SECONDS = 2.0

for i, inst in enumerate(instruments):
    async def _launch_candles(inst_id: str = inst.id, delay: float = i * STAGGER_DELAY_SECONDS) -> None:
        await asyncio.sleep(delay)
        await run_all_candle_pollers(clients[inst_id], states[inst_id], instrument_id=inst_id)

    tg.create_task(_run_rest_poller_isolated(
        _launch_candles(), inst.id, "candle_poller",
    ))
```

### Pattern 3: REST Staleness Detection
**What:** Periodically check if candle or funding data is stale (not updated within threshold), and reset readiness flags to prevent stale snapshots from publishing.
**When to use:** As a periodic background task in main.py, similar to WS staleness in `_mark_stale_instruments`.
**Recommendation:** REST data has naturally longer update intervals than WS. Candle pollers poll every 60-1800 seconds depending on timeframe. Funding polls every 300 seconds. A REST-specific staleness threshold should be longer than WS -- recommend using `max(poll_interval) * 3` or a fixed value like 600 seconds (10 minutes) for candles and 900 seconds (15 minutes) for funding.

Staleness fields needed on `IngestionState`:
- `last_candle_update: datetime | None` -- set in `poll_candles_once` on success
- `last_funding_update: datetime | None` -- already exists on IngestionState

The staleness checker resets `has_candles` or `has_funding` to False when the respective timestamp exceeds the threshold, preventing snapshot publishing via the existing `is_ready()` gate.

```python
REST_CANDLE_STALE_SECONDS = 600   # 10 minutes -- longest normal poll is 1800s/6h but 1m polls every 60s
REST_FUNDING_STALE_SECONDS = 900  # 15 minutes -- normal poll is 300s

def _mark_stale_rest_data(states: dict[str, IngestionState]) -> None:
    now = datetime.now(UTC)
    for instrument_id, state in states.items():
        if state.has_candles and state.last_candle_update is not None:
            elapsed = (now - state.last_candle_update).total_seconds()
            if elapsed > REST_CANDLE_STALE_SECONDS:
                state.has_candles = False
                logger.warning("instrument_candles_stale", instrument=instrument_id, elapsed_seconds=round(elapsed, 1))
        if state.has_funding and state.last_funding_update is not None:
            elapsed = (now - state.last_funding_update).total_seconds()
            if elapsed > REST_FUNDING_STALE_SECONDS:
                state.has_funding = False
                logger.warning("instrument_funding_stale", instrument=instrument_id, elapsed_seconds=round(elapsed, 1))
```

### Pattern 4: Per-Instrument REST Client Instantiation
**What:** Create one `CoinbaseRESTClient` per instrument, sharing a single `RateLimiter` instance (D-08, D-09).
**Where:** In `run_agent()` in main.py, during startup before the TaskGroup.

```python
rate_limiter = RateLimiter()

rest_clients: dict[str, CoinbaseRESTClient] = {}
for inst in instruments:
    rest_clients[inst.id] = CoinbaseRESTClient(
        auth=auth,
        base_url=settings.coinbase.rest_url,
        rate_limiter=rate_limiter,
    )
```

Cleanup in `finally` block must close all clients:
```python
finally:
    for client in rest_clients.values():
        await client.close()
```

### Anti-Patterns to Avoid
- **Modifying run_all_candle_pollers/run_funding_poller signatures heavily:** These functions work fine as-is. The refactor is in main.py wiring, not in the poller implementations.
- **Using a single REST client for all instruments:** Violates D-08 (each instrument gets its own HTTP connection pool for isolation).
- **Sequential instrument polling:** All instruments must poll concurrently via separate tasks, not one after another.
- **Letting one instrument's error kill all pollers:** TaskGroup propagates exceptions by default -- must wrap with error isolation (D-05).

## Rate Budget Analysis

**Current (ETH-PERP only):**
- 5 candle timeframes polling at: 1/60s + 1/120s + 1/300s + 1/600s + 1/1800s = ~0.031 req/s
- 1 funding poller at 1/300s = ~0.003 req/s
- Total: ~0.034 req/s (well under 10 req/s limit)

**Target (5 instruments):**
- 5x candle traffic: ~0.155 req/s
- 5x funding traffic: ~0.017 req/s
- Total: ~0.172 req/s (still well under 10 req/s limit)

**Initial burst concern:** 5 instruments x 5 timeframes = 25 candle requests + 5 funding = 30 requests at startup. With 10 req/s rate limit and 30 max tokens, the stagger (D-01) is essential. At 2-second stagger: each instrument fires 6 requests (5 candle + 1 funding), spread over 8 seconds total = ~3.75 req/s average. The `RateLimiter` with 20% buffer (effective max 24 tokens) will naturally throttle this.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rate limiting | Custom per-instrument limiters | Existing shared `RateLimiter` (D-04, D-09) | Single API key = single rate bucket on Coinbase |
| Instrument enumeration | Hardcoded list | `get_all_instruments()` from instruments.py | Already config-driven from Phase 6 |
| Error isolation in TaskGroup | Manual exception tracking | Simple wrapper coroutine | TaskGroup semantics require wrapping; keep it minimal |

## Common Pitfalls

### Pitfall 1: TaskGroup Exception Propagation
**What goes wrong:** If any task in `asyncio.TaskGroup` raises an unhandled exception, ALL other tasks in the group are cancelled. One instrument's REST client crash kills all instruments.
**Why it happens:** Python 3.11+ TaskGroup cancels the entire group on any unhandled exception.
**How to avoid:** Wrap each per-instrument poller task in a never-propagate exception handler (D-05).
**Warning signs:** "ingestion_task_failed" logs followed by all instruments stopping.

### Pitfall 2: Closure Variable Capture in Loops
**What goes wrong:** When creating async tasks in a `for` loop, the closure captures the loop variable by reference. All tasks end up using the last instrument.
**Why it happens:** Python closures capture variables, not values.
**How to avoid:** Use default argument binding: `async def _launch(inst_id: str = inst.id)` or use `functools.partial`.
**Warning signs:** All instruments show the same instrument_id in logs.

### Pitfall 3: Stale REST Data Not Detected
**What goes wrong:** An instrument's candle or funding poller silently fails (all exceptions caught internally), so `has_candles`/`has_funding` stays True but data is minutes/hours old.
**Why it happens:** The existing `poll_candles_once` catches all exceptions and logs them but doesn't reset readiness flags.
**How to avoid:** Add a `last_candle_update` timestamp to `IngestionState`, set on successful poll, and periodically check staleness (D-07).
**Warning signs:** Snapshots published with old candle/funding data.

### Pitfall 4: Client Cleanup on Shutdown
**What goes wrong:** Multiple REST clients created but not all closed in the finally block, leading to unclosed connection warnings.
**How to avoid:** Store all clients in a dict and iterate to close all in the finally block.

### Pitfall 5: Candle Poller Already Has Internal TaskGroup
**What goes wrong:** `run_all_candle_pollers` spawns its own TaskGroup for 5 timeframes. If we wrap it in an error-isolating wrapper that catches exceptions, the inner TaskGroup's ExceptionGroup might not be handled correctly.
**How to avoid:** The wrapper catches `Exception` which includes `ExceptionGroup`. Ensure the wrapper logs the full exception group, not just the first error. Use `except BaseException` or handle `ExceptionGroup` explicitly.

## Changes Required Per File

### `agents/ingestion/main.py` (primary refactor target)
1. Replace single `rest_client` with per-instrument `rest_clients: dict[str, CoinbaseRESTClient]`
2. Extract shared `RateLimiter` to pass to all clients
3. Replace hardcoded `states["ETH-PERP"]` candle/funding tasks with per-instrument loop
4. Add staggered startup delay per instrument
5. Add error-isolating wrapper for each per-instrument task
6. Add REST staleness checker task
7. Update cleanup in finally block to close all REST clients
8. Update module docstring (remove "currently ETH-PERP only")

### `agents/ingestion/state.py`
1. Add `last_candle_update: datetime | None = None` field for REST staleness tracking

### `agents/ingestion/sources/candles.py`
1. Set `state.last_candle_update = utc_now()` on successful candle fetch (in `poll_candles_once`)
2. Add consecutive failure counter and warning log (D-06)
3. Update docstring: "Coinbase INTX" -> "Coinbase Advanced" per CONTEXT.md naming correction
4. Add `instrument_id` to log fields for multi-instrument distinguishability

### `agents/ingestion/sources/funding_rate.py`
1. `state.last_funding_update` already set on success -- no change needed for staleness
2. Add consecutive failure counter and warning log (D-06)
3. Update docstring: "Coinbase INTX" -> "Coinbase Advanced" per CONTEXT.md naming correction
4. Add `instrument_id` to log fields for multi-instrument distinguishability

### `libs/common/constants.py` (optional)
1. Add `REST_CANDLE_STALE_SECONDS` and `REST_FUNDING_STALE_SECONDS` constants (if not kept local to main.py)

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio 0.23+ |
| Config file | `pyproject.toml` [tool.pytest] section |
| Quick run command | `python -m pytest agents/ingestion/tests/ -x -q` |
| Full suite command | `python -m pytest agents/ingestion/tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MPOL-01 | Candle poller called per-instrument with correct state and client | unit | `python -m pytest agents/ingestion/tests/test_candles.py -x` | Exists but needs multi-instrument tests |
| MPOL-01 | Staggered startup delays between instruments | unit | `python -m pytest agents/ingestion/tests/test_candles.py -x` | Wave 0 |
| MPOL-02 | Funding poller called per-instrument with correct state and publisher | unit | `python -m pytest agents/ingestion/tests/test_funding_rate.py -x` | Exists but needs multi-instrument tests |
| MPOL-01/02 | Error isolation: one instrument crash doesn't kill others | unit | `python -m pytest agents/ingestion/tests/test_main_wiring.py -x` | Wave 0 |
| MPOL-01/02 | Consecutive failure counter logs warning at threshold | unit | `python -m pytest agents/ingestion/tests/test_candles.py -x` | Wave 0 |
| MPOL-01/02 | REST staleness resets readiness flags | unit | `python -m pytest agents/ingestion/tests/test_main_wiring.py -x` | Wave 0 |
| MPOL-01/02 | Per-instrument REST clients share single RateLimiter | unit | `python -m pytest agents/ingestion/tests/test_main_wiring.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest agents/ingestion/tests/ -x -q`
- **Per wave merge:** `python -m pytest agents/ingestion/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `agents/ingestion/tests/test_main_wiring.py` -- test multi-instrument task spawning, error isolation, staleness detection
- [ ] Multi-instrument test cases in existing `test_candles.py` and `test_funding_rate.py` -- verify instrument_id parameter forwarding
- [ ] Test consecutive failure counter and warning log behavior

## Discretion Recommendations

| Area | Recommendation | Rationale |
|------|---------------|-----------|
| Stagger delay | 2 seconds between instruments | 5 instruments x 2s = 8s total. Spreads 30 initial requests comfortably under 10 req/s. |
| Consecutive failure threshold | 5 failures | Matches the example in D-06. For 60s poll interval, 5 failures = 5 minutes of failed data. |
| REST candle staleness | 600 seconds (10 min) | The fastest candle poller runs every 60s. 10 minutes = 10 missed polls, clearly stale. |
| REST funding staleness | 900 seconds (15 min) | Funding polls every 300s. 15 minutes = 3 missed polls. |
| Error isolation structure | Simple wrapper coroutine in main.py | Keeps candles.py and funding_rate.py unchanged (clean, testable). The wrapper just prevents propagation. |
| REST client instantiation | In run_agent() in main.py, loop over instruments | Simple, explicit, easy to understand. No factory abstraction needed for 5 instances. |

## Sources

### Primary (HIGH confidence)
- `agents/ingestion/main.py` -- current wiring, lines 141-153 are the refactor target
- `agents/ingestion/sources/candles.py` -- already parameterized with instrument_id
- `agents/ingestion/sources/funding_rate.py` -- already parameterized with instrument_id
- `agents/ingestion/state.py` -- IngestionState with readiness flags
- `agents/ingestion/sources/ws_market_data.py` -- `_mark_stale_instruments()` reference pattern
- `libs/coinbase/rest_client.py` -- CoinbaseRESTClient constructor accepts shared RateLimiter
- `libs/coinbase/rate_limiter.py` -- token bucket with 30 max tokens, 10/s refill, 20% buffer
- `libs/common/instruments.py` -- `get_all_instruments()` for instrument enumeration

### Secondary (MEDIUM confidence)
- Rate budget calculations based on code-verified poll intervals and documented Coinbase rate limits

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries needed, pure internal refactoring
- Architecture: HIGH -- follows established patterns from Phase 7 (WS multi-instrument), all target code examined
- Pitfalls: HIGH -- based on direct code inspection and Python asyncio TaskGroup semantics

**Research date:** 2026-03-22
**Valid until:** Stable -- internal codebase patterns, no external dependency changes
