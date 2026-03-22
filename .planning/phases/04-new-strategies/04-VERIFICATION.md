---
phase: 04-new-strategies
verified: 2026-03-22T11:04:22Z
status: passed
score: 15/15 must-haves verified
re_verification: false
---

# Phase 04: New Strategies Verification Report

**Phase Goal:** Three new signal sources fill coverage gaps in funding rate dislocations, orderbook flow, and intraday VWAP deviation
**Verified:** 2026-03-22T11:04:22Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | `compute_funding_boost` returns positive boost when funding direction aligns with signal direction | VERIFIED | `funding_filter.py` lines 73-76 check direction alignment; aligned=True path returns boost > 0 |
| 2  | `compute_funding_boost` returns zero boost when funding opposes signal direction (boost-only, never suppress) | VERIFIED | Lines 78-84: `if not aligned: return FundingBoostResult(boost=0.0, ...)` — never suppresses |
| 3  | Z-score computation handles sparse funding rate data gracefully (returns 0.0 for < min_samples) | VERIFIED | Lines 55-56: guard returns `FundingBoostResult(boost=0.0, ...)` when `len(funding_rates) < min_samples` |
| 4  | Time-to-settlement decay increases urgency as next funding settlement approaches | VERIFIED | Lines 87-88: `decay_factor = exp(-2.0 * (1.0 - clamped_hours))` — grows toward 1.0 as hours_since approaches 1.0 |
| 5  | Correlation strategy uses shared funding_filter utility instead of inline implementation | VERIFIED | `correlation.py:30` imports `compute_funding_boost`; called at line 180 |
| 6  | Momentum and mean_reversion strategies opt in to funding boost | VERIFIED | `momentum.py:38,238` and `mean_reversion.py:39,286` both import and call `compute_funding_boost` |
| 7  | OBI strategy emits LONG signal when time-weighted bid/ask imbalance is strongly positive | VERIFIED | `orderbook_imbalance.py:135` `direction = PositionSide.LONG if tw_imbalance > 0`; test_long_signal_positive_imbalance passes |
| 8  | OBI strategy emits SHORT signal when time-weighted bid/ask imbalance is strongly negative | VERIFIED | Same evaluate() path; test_short_signal_negative_imbalance passes |
| 9  | OBI strategy suppresses signals when orderbook is too thin (high spread_bps) | VERIFIED | `orderbook_imbalance.py:127-128`: `if snapshot.spread_bps > p.max_spread_bps: return []`; test_depth_gate_wide_spread passes |
| 10 | OBI strategy averages imbalance over multiple samples (time-weighted, not point-in-time) | VERIFIED | Lines 122-124: `weights = np.arange(1, len(window)+1)`, `tw_imbalance = np.average(window, weights=weights)` |
| 11 | High-conviction OBI signals route to Portfolio A for autonomous execution | VERIFIED | Lines 168-170: `suggested_target = PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction`; test passes |
| 12 | OBI is enabled for all 5 instruments in strategy matrix | VERIFIED | `strategy_matrix.yaml` contains `orderbook_imbalance:` with ETH-PERP, BTC-PERP, SOL-PERP, QQQ-PERP, SPY-PERP |
| 13 | VWAP feasibility validation runs programmatically and auto-decides whether to proceed or defer | VERIFIED | `test_vwap.py` lines 107-220: 4 feasibility tests run; feasibility passed (clamped bar_volumes 8x smoother than price) |
| 14 | VWAP computes session-aware price-volume weighted average with configurable session reset | VERIFIED | `vwap.py:46` `session_reset_hour_utc: int = 0`; `_compute_session_progress()` at line 168; equity instruments use reset hour 14 |
| 15 | VWAP signals have higher reliability later in session (VWAP-04) | VERIFIED | Lines 308 (`session_progress < p.min_session_progress` suppresses early signals) and lines 405-420 (session_score scales conviction with progress) |

**Score:** 15/15 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `agents/signals/funding_filter.py` | Shared funding rate confirmation utility | VERIFIED | 102 lines (min 60); exports `compute_funding_boost` and `FundingBoostResult` |
| `agents/signals/tests/test_funding_filter.py` | Unit tests for funding filter utility | VERIFIED | 125 lines (min 80); 10 test functions covering all behaviors |
| `agents/signals/strategies/orderbook_imbalance.py` | OrderbookImbalanceStrategy SignalStrategy subclass | VERIFIED | 215 lines (min 100); exports `OrderbookImbalanceStrategy`, `OrderbookImbalanceParams` |
| `agents/signals/tests/test_orderbook_imbalance.py` | Unit tests for OBI strategy | VERIFIED | 361 lines (min 100); 14 test functions |
| `configs/strategies/orderbook_imbalance.yaml` | OBI strategy YAML config with per-instrument overrides | VERIFIED | Contains `parameters:` and `instruments:` sections with all 5 instruments |
| `agents/signals/strategies/vwap.py` | VWAP strategy or deferral documentation | VERIFIED | 424 lines (min 30); implements `VWAPStrategy` class (feasibility passed) |
| `agents/signals/tests/test_vwap.py` | Feasibility validation test and strategy tests | VERIFIED | 588 lines (min 50); 16 tests: 4 feasibility + 12 strategy |
| `configs/strategies/vwap.yaml` | VWAP strategy config | VERIFIED | Contains `session_reset_hour_utc`; QQQ-PERP and SPY-PERP set to 14 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `correlation.py` | `funding_filter.py` | `from agents.signals.funding_filter import` | WIRED | Line 30 imports; called at line 180 |
| `momentum.py` | `funding_filter.py` | `from agents.signals.funding_filter import` | WIRED | Line 38 imports; called at line 238 |
| `mean_reversion.py` | `funding_filter.py` | `from agents.signals.funding_filter import` | WIRED | Line 39 imports; called at line 286 |
| `main.py` | `orderbook_imbalance.py` | `STRATEGY_CLASSES["orderbook_imbalance"]` | WIRED | Line 59: `"orderbook_imbalance": OrderbookImbalanceStrategy` |
| `strategy_matrix.yaml` | `orderbook_imbalance.py` | strategy matrix enablement | WIRED | `orderbook_imbalance:` entry with all 5 instruments |
| `orderbook_imbalance.py` | `feature_store.py` | `store.orderbook_imbalances` | WIRED | Line 115: `imbalances = store.orderbook_imbalances` |
| `main.py` | `vwap.py` | `STRATEGY_CLASSES["vwap"]` | WIRED | Lines 60, 70: `"vwap": VWAPStrategy` and `"vwap": VWAPParams` |
| `strategy_matrix.yaml` | `vwap.py` | strategy matrix enablement | WIRED | `vwap:` entry with all 5 instruments |
| `vwap.py` | `feature_store.py` | `store.bar_volumes, store.closes, store.timestamps` | WIRED | Lines 267, 270, 279 use all three properties |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FUND-01 | 04-01 | Funding rate conviction boost for aligned signals | SATISFIED | `compute_funding_boost` boosts on alignment; integrated in correlation, momentum, mean_reversion |
| FUND-02 | 04-01 | Rolling z-score of funding rate vs historical distribution | SATISFIED | `funding_filter.py:59-66`: window z-score computation with ddof=1 |
| FUND-03 | 04-01 | Time-to-funding decay — urgency increases near settlement | SATISFIED | `funding_filter.py:87-88`: `exp(-2.0 * (1.0 - clamped_hours))` |
| OBI-01 | 04-02 | New strategy using bid/ask depth imbalance as directional signal | SATISFIED | `OrderbookImbalanceStrategy` fully implemented and registered |
| OBI-02 | 04-02 | Time-weighted imbalance over multiple samples | SATISFIED | `orderbook_imbalance.py:122-124`: linear weights via `np.arange` |
| OBI-03 | 04-02 | Minimum depth gate suppresses thin orderbook signals | SATISFIED | `orderbook_imbalance.py:127-128`: `spread_bps > max_spread_bps` gate |
| OBI-04 | 04-02 | Portfolio A routing for short time horizon signals | SATISFIED | Lines 168-170: `PortfolioTarget.A` when `conviction >= portfolio_a_min_conviction` |
| VWAP-01 | 04-03 | Feasibility validation for bar_volumes VWAP approximation | SATISFIED | 4 programmatic feasibility tests; implementation proceeded (feasibility passed) |
| VWAP-02 | 04-03 | Session VWAP with configurable reset (00:00 UTC crypto, 09:30 ET equity) | SATISFIED | `session_reset_hour_utc` param; QQQ/SPY set to 14 in config |
| VWAP-03 | 04-03 | Extreme deviation from session VWAP triggers mean reversion signal | SATISFIED | `vwap.py:308-340`: deviation > threshold triggers LONG/SHORT signal |
| VWAP-04 | 04-03 | VWAP signals more reliable later in session | SATISFIED | Early session suppression (`session_progress < min_session_progress`) and conviction scaling with `session_score` |

All 11 requirement IDs declared in PLAN frontmatter are satisfied. No orphaned requirements found for Phase 04 in REQUIREMENTS.md.

### Anti-Patterns Found

None. No TODO, FIXME, PLACEHOLDER comments or empty implementations found in any of the new or modified files.

### Human Verification Required

None. All verifiable behaviors are covered by automated tests that pass.

### Test Results

| Test File | Tests | Outcome |
|-----------|-------|---------|
| `test_funding_filter.py` | 10 | All pass |
| `test_orderbook_imbalance.py` | 14 | All pass |
| `test_vwap.py` | 16 (4 feasibility + 12 strategy) | All pass (2 expected RuntimeWarnings for divide) |
| `test_correlation.py` (regression) | 85 total (with momentum, mean_reversion) | All pass |

### Summary

Phase 04 fully achieves its goal. Three new signal sources are implemented, tested, wired, and configured:

1. **Funding Rate Filter (FUND-01/02/03):** Shared `compute_funding_boost` utility with z-score analysis, direction alignment, and settlement time decay. Integrated into correlation (refactored from inline), momentum, and mean reversion as opt-in boost. Boost-only semantics verified — opposing funding returns 0.0 and never suppresses.

2. **Orderbook Imbalance Strategy (OBI-01/02/03/04):** New `OrderbookImbalanceStrategy` with time-weighted bid/ask imbalance (linear weights), spread-based depth gate, 3-component conviction model, and Portfolio A routing for high-conviction signals. Registered in `main.py` and enabled for all 5 instruments in the strategy matrix with per-instrument parameter tuning.

3. **VWAP Deviation Strategy (VWAP-01/02/03/04):** Feasibility validated programmatically (48% negative bar_volumes clamped to 0; resulting VWAP is 8x smoother than raw price). Full `VWAPStrategy` implemented with session-aware reset (00:00 UTC crypto, 14:00 UTC equity), early session suppression, deviation-based mean reversion signals, and time-of-session conviction scaling. Registered and enabled for all 5 instruments.

---

_Verified: 2026-03-22T11:04:22Z_
_Verifier: Claude (gsd-verifier)_
