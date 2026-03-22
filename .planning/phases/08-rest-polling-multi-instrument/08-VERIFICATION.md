---
phase: 08-rest-polling-multi-instrument
verified: 2026-03-22T20:15:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 8: REST Polling Multi-Instrument Verification Report

**Phase Goal:** Refactor REST-polled data sources (candles, funding rates) to fetch all active instruments instead of only ETH-PERP
**Verified:** 2026-03-22T20:15:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth                                                                              | Status     | Evidence                                                                                             |
|----|------------------------------------------------------------------------------------|------------|------------------------------------------------------------------------------------------------------|
| 1  | Candle poller fetches candles for all 5 active instruments concurrently            | VERIFIED  | `main.py` lines 199-211: `for i, inst in enumerate(instruments)` spawns per-instrument candle task  |
| 2  | Funding rate poller fetches funding for all 5 active instruments concurrently      | VERIFIED  | `main.py` lines 214-227: `for i, inst in enumerate(instruments)` spawns per-instrument funding task |
| 3  | Each instrument has its own CoinbaseRESTClient sharing one RateLimiter             | VERIFIED  | `main.py` lines 119-128: `rate_limiter = RateLimiter()` then `rest_clients[inst.id] = CoinbaseRESTClient(..., rate_limiter=rate_limiter)` |
| 4  | Instrument pollers start with staggered delays to avoid initial burst              | VERIFIED  | `main.py` lines 202-204: `delay: float = i * REST_POLLER_STAGGER_SECONDS` + `await asyncio.sleep(delay)` |
| 5  | One instrument's poller crash does not kill the other instruments                  | VERIFIED  | `main.py` lines 50-66: `_run_rest_poller_isolated` catches all exceptions without re-raising; 2 tests confirm behavior |
| 6  | Consecutive poller failures are tracked and logged at threshold                    | VERIFIED  | `candles.py` lines 102-118: `consecutive_failures` counter with warning at `>= 5`; same in `funding_rate.py` lines 113-128 |
| 7  | Stale REST data resets readiness flags to prevent snapshot publishing              | VERIFIED  | `main.py` lines 69-90: `_mark_stale_rest_data` resets `has_candles`/`has_funding` on threshold breach; staleness loop at line 230-234 |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact                                          | Expected                                              | Status   | Details                                                                      |
|---------------------------------------------------|-------------------------------------------------------|----------|------------------------------------------------------------------------------|
| `agents/ingestion/main.py`                        | Multi-instrument REST polling orchestration           | VERIFIED | Contains `_run_rest_poller_isolated`, `_mark_stale_rest_data`, per-instrument loops, `rest_clients` dict, staggered starts |
| `agents/ingestion/state.py`                       | REST staleness timestamp field                        | VERIFIED | `last_candle_update: datetime | None = None` at line 59                      |
| `agents/ingestion/sources/candles.py`             | Consecutive failure tracking, staleness timestamp, instrument logging | VERIFIED | `consecutive_failures` counter, `state.last_candle_update = utc_now()`, `instrument=instrument_id` in all logger calls |
| `agents/ingestion/sources/funding_rate.py`        | Consecutive failure tracking, instrument logging      | VERIFIED | `consecutive_failures` counter, `instrument=instrument_id` in all logger calls |
| `agents/ingestion/tests/test_main_wiring.py`      | Multi-instrument wiring tests                         | VERIFIED | 6 tests: 4 for `_mark_stale_rest_data`, 2 for `_run_rest_poller_isolated`    |
| `libs/common/constants.py`                        | REST staleness threshold constants                    | VERIFIED | `REST_CANDLE_STALE_SECONDS = 600`, `REST_FUNDING_STALE_SECONDS = 900`, `REST_POLLER_STAGGER_SECONDS = 2.0` |

### Key Link Verification

| From                              | To                                           | Via                                              | Status   | Details                                                                                    |
|-----------------------------------|----------------------------------------------|--------------------------------------------------|----------|--------------------------------------------------------------------------------------------|
| `agents/ingestion/main.py`        | `agents/ingestion/sources/candles.py`        | per-instrument `run_all_candle_pollers` calls    | WIRED   | `run_all_candle_pollers(rest_clients[inst_id], states[inst_id], instrument_id=inst_id)` at lines 205-207 |
| `agents/ingestion/main.py`        | `agents/ingestion/sources/funding_rate.py`   | per-instrument `run_funding_poller` calls        | WIRED   | `run_funding_poller(rest_clients[inst_id], states[inst_id], publisher, instrument_id=inst_id)` at lines 220-222 |
| `agents/ingestion/main.py`        | `agents/ingestion/state.py`                  | REST staleness checker reads `last_candle_update`/`last_funding_update` | WIRED   | `_mark_stale_rest_data` at lines 73, 82 reads both fields; `state.py` declares both fields |

Note: The PLAN specified regex patterns `run_all_candle_pollers.*rest_clients\[` and `run_funding_poller.*rest_clients\[` which don't match because the calls are inside inner closures (`_launch_candles`, `_launch_funding`). The actual wiring is substantively correct — the per-instrument client is passed as the first argument in both cases.

### Requirements Coverage

| Requirement | Source Plan | Description                                                                              | Status    | Evidence                                                                                               |
|-------------|-------------|------------------------------------------------------------------------------------------|-----------|--------------------------------------------------------------------------------------------------------|
| MPOL-01     | 08-01-PLAN  | Candle poller fetches candles for each active instrument independently (5 instruments x N timeframes concurrent) | SATISFIED | `main.py` spawns 5 isolated candle poller tasks via per-instrument loop; each calls `run_all_candle_pollers` with its own REST client |
| MPOL-02     | 08-01-PLAN  | Funding rate poller fetches funding for each active instrument independently (5 concurrent pollers) | SATISFIED | `main.py` spawns 5 isolated funding poller tasks via per-instrument loop; each calls `run_funding_poller` with its own REST client |

No orphaned requirements — both MPOL-01 and MPOL-02 are claimed in plan 08-01 and satisfied.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `agents/ingestion/main.py` | 108 | `"ETH-PERP"` in a comment example | Info | Comment illustrating product-to-instrument mapping; not a hardcoded operational reference — no impact |

No blocker or warning anti-patterns found. `grep -c "ETH-PERP" agents/ingestion/main.py` returns 1, which is the benign comment on line 108. All operational code uses `inst.id` dynamically.

### Human Verification Required

None — all goal outcomes are verifiable programmatically.

### Test Suite

All 86 ingestion tests pass (`agents/ingestion/tests/ -x -q`). Module imports cleanly:
`from agents.ingestion.main import run_agent, _run_rest_poller_isolated, _mark_stale_rest_data` — OK.

### Git Commits

All 4 task commits from SUMMARY are present and valid:
- `78d7361` — feat(08-01): add staleness timestamps and consecutive failure tracking
- `5d47b51` — feat(08-01): refactor main.py for multi-instrument REST polling
- `593883f` — test(08-01): add multi-instrument REST polling wiring tests
- `f62a2dd` — fix(08-01): replace remaining Coinbase INTX docstring references

### Gaps Summary

No gaps. All 7 observable truths verified, all artifacts substantive and wired, both requirements satisfied, test suite green.

---

_Verified: 2026-03-22T20:15:00Z_
_Verifier: Claude (gsd-verifier)_
