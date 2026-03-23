# Phase 9: End-to-End Verification - Research

**Researched:** 2026-03-23
**Domain:** Integration testing, runtime assertions, dashboard enhancement (Python/pytest/asyncio)
**Confidence:** HIGH

## Summary

Phase 9 is a verification-only phase -- no new pipeline features, just tests, assertions, and dashboard enhancements that prove all 5 instruments flow through ingestion into the signals agent. The codebase is well-structured for this: `on_ws_update()` takes an `instrument_id` parameter, `build_snapshot()` reads `state.instrument_id`, and the signals agent routes snapshots to per-instrument FeatureStores via `stores.get(snapshot.instrument)`. The work is straightforward integration testing with known patterns already established in the project.

The existing test infrastructure (`test_main_wiring.py`, `test_feature_store.py`, `conftest.py` with all 5 instruments loaded) provides direct patterns to extend. The dashboard already reads from Redis streams and has ANSI formatting helpers. Adding per-instrument tables requires only new `_format_*` functions and new `_get_*` data fetchers following the established pattern.

**Primary recommendation:** Extend existing test files with multi-instrument E2E flow tests, add lightweight runtime assertions in `on_ws_update()` and `build_snapshot()`, and add two new dashboard sections for per-instrument snapshot status and FeatureStore sample counts.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Automated integration tests covering the full wiring (ingestion -> snapshot -> signals FeatureStore) PLUS dashboard enhancement showing per-instrument status
- **D-02:** No standalone verification script -- the dashboard serves as the live environment health check
- **D-03:** Integration tests in `agents/ingestion/tests/` that verify `on_ws_update()` with 5 different instrument states produces 5 snapshots with correct instrument fields (ME2E-01)
- **D-04:** Tests feed known instrument data through the normalizer and assert `snapshot.instrument` matches the expected instrument ID for each of the 5 instruments
- **D-05:** Integration tests in `agents/signals/tests/` that verify the signals agent routes snapshots to the correct per-instrument FeatureStores (ME2E-02)
- **D-06:** Tests verify that FeatureStore `sample_count` is non-zero for all 5 instruments after feeding snapshots
- **D-07:** Tests verify that strategies fire (evaluate returns signals) for instruments where the strategy is enabled per `strategy_matrix.yaml`
- **D-08:** Add per-instrument snapshot table to existing `scripts/dashboard.py` showing: instrument_id, last_snapshot_time, mark_price, spread_bps, funding_rate, stale status
- **D-09:** Add FeatureStore section showing `sample_count` per instrument -- proves signals agent is consuming data for all 5 instruments
- **D-10:** Dashboard follows existing auto-refresh pattern (no one-shot mode)
- **D-11:** Runtime assertion in `on_ws_update()` that validates `instrument_id` matches `states[instrument_id].instrument_id` before calling `build_snapshot()`
- **D-12:** Runtime assertion in `build_snapshot()` that validates `state.instrument_id` equals the instrument parameter passed by the caller
- **D-13:** Both assertions are lightweight (dict key lookup comparison) -- no production performance concern
- **D-14:** Integration tests specifically verify no snapshot carries a mismatched instrument ID when processing data for all 5 instruments concurrently

### Claude's Discretion
- Test fixture design (shared helpers vs per-test setup)
- Dashboard layout and formatting details
- Assertion error message wording
- Whether assertions use `assert` statements or raise custom exceptions

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ME2E-01 | All active instruments produce MarketSnapshots published to stream:market_snapshots with correct instrument field | D-03, D-04, D-11, D-12, D-14: Integration tests in ingestion + runtime assertions verify correct instrument ID flows through snapshot creation |
| ME2E-02 | Signals agent FeatureStores receive samples for all active instruments (store_samples shows non-zero for all 5) | D-05, D-06, D-07: Integration tests in signals verify routing + sample accumulation + strategy firing per matrix |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 8+ | Test runner | Already used across project |
| pytest-asyncio | 0.23+ | Async test support | Already used for async tests |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| freezegun | 1.4+ | Time freezing | Deterministic timestamps in snapshot creation |
| redis.asyncio | 5.0+ | Dashboard Redis reads | Already used in dashboard.py |
| orjson | 3.9+ | JSON parsing in dashboard | Already used in dashboard.py |

No new dependencies needed. All libraries are already in the project.

## Architecture Patterns

### Test Structure
```
agents/ingestion/tests/
    test_main_wiring.py    # EXTEND: add E2E multi-instrument flow tests
agents/signals/tests/
    test_main.py           # EXTEND: add multi-instrument routing tests
    conftest.py            # Already loads all 5 instruments into registry
scripts/
    dashboard.py           # EXTEND: add per-instrument sections
agents/ingestion/
    main.py                # MODIFY: add assertion in on_ws_update()
    normalizer.py          # MODIFY: add assertion in build_snapshot()
```

### Pattern 1: Multi-Instrument Test Fixture
**What:** Create IngestionState objects for all 5 instruments with data that satisfies `is_ready()` and `has_minimum_data()`, then call `on_ws_update()` for each.
**When to use:** All ingestion E2E tests (D-03, D-04, D-14).
**Example:**
```python
ALL_INSTRUMENTS = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]

def _ready_state(instrument_id: str) -> IngestionState:
    """Create an IngestionState that passes is_ready() and has_minimum_data()."""
    state = IngestionState(instrument_id=instrument_id)
    state.has_ws_tick = True
    state.has_candles = True
    state.has_funding = True
    state.best_bid = Decimal("100.00")
    state.best_ask = Decimal("100.50")
    state.last_price = Decimal("100.25")
    state.mark_price = Decimal("100.30")
    return state
```

### Pattern 2: Snapshot Instrument Field Verification
**What:** After building snapshots for all 5 instruments, verify each snapshot's `instrument` field matches its source instrument ID.
**When to use:** D-04, D-14.
**Example:**
```python
for instrument_id in ALL_INSTRUMENTS:
    snapshot = build_snapshot(states[instrument_id])
    assert snapshot is not None
    assert snapshot.instrument == instrument_id
```

### Pattern 3: FeatureStore Routing Verification
**What:** Create per-instrument FeatureStores matching signals agent pattern, feed snapshots with different instrument IDs, verify each store accumulates samples only for its instrument.
**When to use:** D-05, D-06.
**Example:**
```python
stores = {iid: FeatureStore(sample_interval=timedelta(seconds=0)) for iid in ALL_INSTRUMENTS}

for iid in ALL_INSTRUMENTS:
    snap = _snap(ts, instrument=iid)
    store = stores.get(snap.instrument)
    assert store is not None
    store.update(snap)

for iid in ALL_INSTRUMENTS:
    assert stores[iid].sample_count > 0
```

### Pattern 4: Dashboard Per-Instrument Section
**What:** New `_format_instrument_snapshots()` function reading recent snapshots from Redis, grouping by instrument, showing last update time and stale status.
**When to use:** D-08, D-09.
**Example:**
```python
async def _get_per_instrument_snapshots(r: aioredis.Redis, count: int = 100) -> dict[str, dict[str, Any]]:
    """Group recent snapshots by instrument, keeping latest for each."""
    entries = await r.xrevrange("stream:market_snapshots", "+", "-", count=count)
    by_instrument: dict[str, dict[str, Any]] = {}
    for _, fields in entries:
        parsed = _parse_entry(fields)
        if parsed and parsed.get("instrument") not in by_instrument:
            by_instrument[parsed["instrument"]] = parsed
    return by_instrument
```

### Anti-Patterns to Avoid
- **Mocking Redis for dashboard tests:** The dashboard sections are pure formatting functions. Test them by calling the format functions directly with mock data, not by spinning up fakeredis. Dashboard tests are out of scope per CONTEXT.md decisions.
- **Using `assert` for runtime validation in hot path without measurement:** The assertions in `on_ws_update()` and `build_snapshot()` are a dict key comparison -- O(1), negligible. But do not add heavier validation.
- **Modifying `build_snapshot()` signature:** D-12 says validate `state.instrument_id` against the instrument parameter. But `build_snapshot()` currently takes only `state`. The assertion should compare `state.instrument_id` against itself being non-empty, or add an optional `instrument_id` parameter for cross-validation. Recommendation: add an `instrument_id` parameter to `build_snapshot()` for explicit cross-check.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Test snapshot creation | Custom snapshot builders per test | Extend existing `_snap()` helper from `test_feature_store.py` with `instrument` parameter | Consistent, DRY, already tested |
| Dashboard table formatting | Custom column alignment | f-string formatting with fixed widths (existing pattern in `_format_stream_table()`) | Already established in dashboard |

## Common Pitfalls

### Pitfall 1: FeatureStore sample_interval blocks test
**What goes wrong:** FeatureStore has a 30-second sample interval in production. Tests feeding multiple snapshots with close timestamps get skipped.
**Why it happens:** Default `sample_interval=timedelta(seconds=30)` rejects snapshots within 30s of each other.
**How to avoid:** In tests, use `sample_interval=timedelta(seconds=0)` or space timestamps 30+ seconds apart.
**Warning signs:** `store.sample_count` stays at 1 despite feeding multiple snapshots.

### Pitfall 2: _snap() helper hardcodes instrument
**What goes wrong:** The `_snap()` helper in `test_feature_store.py` hardcodes `instrument=TEST_INSTRUMENT_ID` ("ETH-PERP"). Multi-instrument tests produce all snapshots with the same instrument.
**Why it happens:** Helper was written before multi-instrument support.
**How to avoid:** Add `instrument: str = TEST_INSTRUMENT_ID` parameter to `_snap()`, or create a new helper that accepts instrument.
**Warning signs:** All FeatureStores show sample_count=0 except ETH-PERP.

### Pitfall 3: on_ws_update() requires publisher mock
**What goes wrong:** `on_ws_update()` is a nested closure inside `run_agent()` that captures `publisher`, `states`, `_last_publish`, and `snapshot_count`. It cannot be tested directly without refactoring.
**Why it happens:** The function is defined inline in `run_agent()`.
**How to avoid:** Test the E2E flow by calling `build_snapshot()` directly for D-03/D-04 (unit-level). For the runtime assertion (D-11), add it to `on_ws_update()` and verify via a dedicated test that constructs the closure dependencies manually, OR test indirectly by verifying `build_snapshot()` with a cross-check parameter.
**Warning signs:** ImportError when trying to import `on_ws_update` directly.

### Pitfall 4: Instrument registry not loaded in ingestion tests
**What goes wrong:** Tests calling `get_all_instruments()` fail with empty registry.
**Why it happens:** Ingestion tests don't have `conftest.py` with `load_instruments()` call.
**How to avoid:** Either add a conftest.py to `agents/ingestion/tests/` that loads instruments, or use the pattern from `agents/signals/tests/conftest.py`.
**Warning signs:** `get_all_instruments()` returns empty list.

### Pitfall 5: build_snapshot() signature change affects callers
**What goes wrong:** Adding `instrument_id` parameter to `build_snapshot()` breaks existing callers.
**Why it happens:** Currently only takes `state: IngestionState`.
**How to avoid:** Make the parameter optional with default `None`. When provided, assert against `state.instrument_id`. The caller in `on_ws_update()` passes the `instrument_id` it received.
**Warning signs:** TypeError on existing code paths.

## Code Examples

### Example 1: Runtime assertion in on_ws_update()
```python
async def on_ws_update(instrument_id: str) -> None:
    """Called on every WS state update -- build and publish a throttled snapshot."""
    nonlocal snapshot_count

    # Throttle check (existing)
    now = time.monotonic()
    if now - _last_publish.get(instrument_id, 0.0) < _THROTTLE_SECONDS:
        return
    _last_publish[instrument_id] = now

    state = states[instrument_id]

    # D-11: Validate instrument ID consistency before snapshot creation
    assert state.instrument_id == instrument_id, (
        f"Instrument ID mismatch: state has {state.instrument_id!r}, "
        f"callback received {instrument_id!r}"
    )

    # ... rest unchanged
```

### Example 2: Runtime assertion in build_snapshot()
```python
def build_snapshot(
    state: IngestionState,
    instrument_id: str | None = None,
) -> MarketSnapshot | None:
    """Build a MarketSnapshot from the current ingestion state.

    Args:
        state: Current shared ingestion state.
        instrument_id: Optional cross-check -- if provided, asserts
            it matches state.instrument_id (D-12).
    """
    if instrument_id is not None:
        assert state.instrument_id == instrument_id, (
            f"Instrument ID mismatch in build_snapshot: "
            f"state={state.instrument_id!r}, param={instrument_id!r}"
        )

    if not state.has_minimum_data():
        return None
    # ... rest unchanged
```

### Example 3: Dashboard per-instrument table
```python
def _format_instrument_snapshots(
    per_instrument: dict[str, dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    lines.append(f"  {DIM}{'Instrument':<12} {'Mark Price':>12} {'Spread':>8} {'Funding':>10} {'Age':>8} {'Status':>8}{RESET}")
    lines.append(f"  {DIM}{'─' * 12} {'─' * 12} {'─' * 8} {'─' * 10} {'─' * 8} {'─' * 8}{RESET}")

    for iid in ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]:
        snap = per_instrument.get(iid)
        if not snap:
            lines.append(f"  {DIM}{iid:<12} {'--':>12} {'--':>8} {'--':>10} {'--':>8} {'NONE':>8}{RESET}")
            continue
        # ... format fields from snap dict
    return lines
```

## State of the Art

No technology changes relevant to this phase. All patterns use existing project infrastructure.

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single-instrument tests | Multi-instrument test fixtures via conftest.py | Phase 6-8 | Tests must cover all 5 instruments |
| Hardcoded ETH-PERP in test helpers | Parameterized instrument ID | Phase 9 (this work) | _snap() helper needs instrument parameter |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio 0.23+ |
| Config file | pyproject.toml (existing) |
| Quick run command | `python -m pytest agents/ingestion/tests/test_main_wiring.py agents/signals/tests/test_main.py -x -q` |
| Full suite command | `python -m pytest agents/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ME2E-01 | All 5 instruments produce snapshots with correct instrument field | integration | `python -m pytest agents/ingestion/tests/test_main_wiring.py -x -q -k "e2e or instrument"` | Partially (extend existing) |
| ME2E-02 | FeatureStores receive samples for all 5 instruments | integration | `python -m pytest agents/signals/tests/test_main.py -x -q -k "routing or instrument"` | Partially (extend existing) |

### Sampling Rate
- **Per task commit:** `python -m pytest agents/ingestion/tests/ agents/signals/tests/ -x -q`
- **Per wave merge:** `python -m pytest agents/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] Extend `_snap()` in `test_feature_store.py` to accept `instrument` parameter -- or create shared helper
- [ ] Add `conftest.py` to `agents/ingestion/tests/` with instrument registry setup (if not present)

## Open Questions

1. **Assert statement vs custom exception for runtime checks (D-11, D-12)**
   - What we know: User decision D-13 says "lightweight" and leaves assert vs exception to Claude's discretion
   - What's unclear: Whether `assert` statements are stripped in production (`python -O` mode)
   - Recommendation: Use `assert` statements. The project does not use `-O` flag (Docker CMD runs `python -m agents.ingestion.main`, no optimization flags). Existing normalizer.py already uses `assert` (lines 43-53). Consistent with codebase convention.

2. **Dashboard FeatureStore data source (D-09)**
   - What we know: The dashboard reads from Redis streams. FeatureStore sample counts are in-memory in the signals agent, not published to Redis.
   - What's unclear: How dashboard gets FeatureStore sample counts without a new Redis key
   - Recommendation: The signals agent already logs `store_samples` dict every 500 snapshots (line 440-443 of signals/main.py). For the dashboard, publish a periodic `stream:signals_status` message with sample counts, or read the `signals_progress` structured log. Simpler: add a new Redis hash `phantom:feature_store_status` that the signals agent updates periodically. The dashboard reads this hash. This is a minor addition -- a single `HSET` call per instrument every N snapshots.

## Sources

### Primary (HIGH confidence)
- `agents/ingestion/main.py` -- on_ws_update() closure structure, states dict, throttle logic
- `agents/ingestion/normalizer.py` -- build_snapshot() signature and instrument field assignment
- `agents/ingestion/state.py` -- IngestionState with instrument_id, readiness flags
- `agents/signals/main.py` -- Per-instrument stores dict, routing logic, strategy execution
- `agents/signals/feature_store.py` -- FeatureStore.update(), sample_count property
- `agents/ingestion/tests/test_main_wiring.py` -- Existing test patterns for staleness, isolation
- `agents/signals/tests/test_main.py` -- Serialization test patterns
- `agents/signals/tests/test_feature_store.py` -- _snap() helper, sample interval behavior
- `agents/signals/tests/conftest.py` -- All 5 instruments loaded via load_instruments()
- `scripts/dashboard.py` -- Dashboard structure, section renderers, Redis data fetching
- `configs/strategy_matrix.yaml` -- Per-instrument strategy enablement

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all existing
- Architecture: HIGH -- extending established patterns with known integration points
- Pitfalls: HIGH -- identified from direct code inspection of existing test infrastructure

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable -- verification phase, no moving targets)
