---
phase: 22-data-pipeline-fixes
plan: "02"
subsystem: signals, ingestion, coinbase
tags: [bar_volumes, vwap, index_price, graceful-degradation, data-pipeline]
dependency_graph:
  requires: [22-01]
  provides: [correct-bar-volumes, real-index-price, strategy-sentinel-guards]
  affects: [agents/signals, agents/ingestion, libs/coinbase]
tech_stack:
  added: []
  patterns: [zero-sentinel-for-unavailable-data, candle-volume-per-bar]
key_files:
  created: []
  modified:
    - libs/common/models/market_snapshot.py
    - libs/coinbase/models.py
    - libs/coinbase/rest_client.py
    - agents/ingestion/sources/funding_rate.py
    - agents/ingestion/normalizer.py
    - agents/signals/feature_store.py
    - agents/signals/strategies/vwap.py
    - agents/signals/strategies/regime_trend.py
    - agents/signals/tests/test_feature_store.py
    - agents/signals/tests/test_regime_trend.py
    - agents/signals/tests/test_correlation.py
decisions:
  - "candle_volume_1m sourced from ONE_MINUTE candle data (not np.diff of 24h rolling)"
  - "index_price uses Decimal('0') sentinel when exchange API provides none — no fallback to last_price"
  - "VWAP strategy alignment updated: bar_vols now same length as closes (not N-1 diffs)"
  - "RegimeTrendStrategy guards on entire spot_ema_period window, not just last sample"
metrics:
  duration_minutes: 4
  completed_date: "2026-04-09"
  tasks_completed: 3
  files_modified: 11
---

# Phase 22 Plan 02: Bar Volumes and Index Price Fix Summary

Fixed `bar_volumes` to use true per-bar candle volumes from `candle_volume_1m` field (eliminating ~48% negative entries from np.diff), and `index_price` to source from exchange API with a `Decimal("0")` sentinel for graceful degradation when unavailable.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 | b58839c | PROF-03: candle_volume_1m field + bar_volumes from candle data |
| 2 | 3506592 | ROBU-05 part 1: index_price from exchange API with zero sentinel |
| 3 | 91d8aca | ROBU-05 part 2: graceful degradation guards in RegimeTrend + Correlation |

## What Was Built

### Task 1 — PROF-03: Bar Volume Fix

`bar_volumes` previously computed `np.diff` of 24h rolling volume, producing ~48% negative values that corrupted VWAP weights.

**Fix:** Added `candle_volume_1m: Decimal = Decimal("0")` to `MarketSnapshot` (last field, default, no positional breakage). The normalizer populates it from `state.candles_by_granularity["ONE_MINUTE"][-1].volume`. `FeatureStore` stores it in a new `_bar_volumes: deque[float]` and the `bar_volumes` property returns the deque directly — always non-negative, same length as `closes`.

`to_checkpoint()` serializes `bar_volumes`; `from_checkpoint()` restores via `.get("bar_volumes", [])` for backward compat with existing checkpoints.

**Deviation — VWAP alignment fix (Rule 1):** The VWAP strategy had `closes[1:]`/`bv_start - 1` alignment tied to the old N-1 diff length. Updated to use `closes` directly with `bv_start = session_start_idx` since bar_vols is now the same length as closes.

### Task 2 — ROBU-05 part 1: Real Index Price

`index_price` was silently set to `last_price` when the API returned none, making basis calculations meaningless.

**Fix:** Added `index_price: Decimal = Decimal("0")` to `FundingRateResponse`. `get_funding_rate()` now extracts `perp_details.get("index_price") or perp_details.get("base_asset_index_price")`, defaulting to `"0"` sentinel. `funding_rate.py` only sets `state.index_price` when `resp.index_price > 0` — no last_price fallback. `normalizer.py` uses `Decimal("0")` when `state.index_price is None` instead of `state.last_price`.

### Task 3 — ROBU-05 part 2: Strategy Guards

**RegimeTrendStrategy:** Added explicit guard after `index_prices = store.index_prices` — returns `[]` if any value in the last `spot_ema_period` samples is `0.0`. Logs `regime_trend_no_index_price` at debug level.

**CorrelationStrategy:** Already handles zero index_price correctly via `np.where(index_prices > 0, ..., 0.0)` in `_compute_basis_series`. All z-scores are zero, no threshold trigger, strategy naturally returns `[]`. Verified by test.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] VWAP strategy alignment after bar_volumes length change**
- **Found during:** Task 1
- **Issue:** `vwap.py` used `closes[1:]` and `bv_start = session_start_idx - 1` because bar_volumes was N-1 in length (np.diff). After bar_volumes became length N, the alignment was wrong.
- **Fix:** Updated to use `closes` directly and `bv_start = session_start_idx`
- **Files modified:** `agents/signals/strategies/vwap.py`
- **Commit:** b58839c

## Known Stubs

None — all data paths are wired: `candle_volume_1m` flows from normalizer → MarketSnapshot → FeatureStore → VWAP strategy. `index_price` flows from REST API → FundingRateResponse → state → normalizer → MarketSnapshot → FeatureStore → strategies.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes at trust boundaries beyond what the plan's threat model covers. `Decimal(str(index_price_str))` in `rest_client.py` sanitizes external API input (T-22-03 mitigated).

## Test Results

- `agents/signals/tests/test_feature_store.py` — 10 passed
- `agents/signals/tests/test_vwap.py` — 21 passed
- `agents/signals/tests/test_regime_trend.py` — 33 passed (includes new guard test)
- `agents/signals/tests/test_correlation.py` — 30 passed (includes new guard test)
- Full regression (excluding 2 pre-existing failures): **1269 passed, 5 skipped**

Pre-existing failures (unrelated to this plan):
- `test_liquidation_cascade.py::test_oi_drop_with_price_dump_signals_long_fade`
- `test_momentum.py::TestMomentumVolumeFilter::test_low_volume_rejects_signal`

## Self-Check: PASSED

- [x] `libs/common/models/market_snapshot.py` exists and contains `candle_volume_1m`
- [x] `agents/signals/feature_store.py` exists and contains `_bar_volumes`
- [x] `agents/ingestion/normalizer.py` contains `Decimal("0")` sentinel for index_price
- [x] Commits b58839c, 3506592, 91d8aca confirmed in git log
