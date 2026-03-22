---
phase: 03-liquidation-correlation-regime
verified: 2026-03-22T10:30:00Z
status: passed
score: 12/12 must-haves verified
re_verification: false
---

# Phase 03: Liquidation, Correlation, Regime Verification Report

**Phase Goal:** The remaining three existing strategies produce more nuanced signals with graduated responses, multi-window analysis, and adaptive thresholds
**Verified:** 2026-03-22T10:30:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Liquidation cascade classifies OI drops into Tier 1 (2-4%), Tier 2 (4-8%), Tier 3 (>8%) with different stop/TP widths | VERIFIED | `_classify_tier()` static method at line 157; tier-specific `tierN_stop_atr_mult` / `tierN_tp_atr_mult` applied via `getattr(p, f"tier{tier}_stop_atr_mult")` at line 244 |
| 2 | Tier 3 severe cascades produce wider stops, bigger targets, and baseline higher conviction than Tier 1 | VERIFIED | T1: 1.5/2.0 ATR, T2: 2.0/3.0 ATR, T3: 3.0/4.5 ATR in params; tier_boost dict `{1: 0.0, 2: 0.05, 3: 0.10}` at line 366 |
| 3 | Signals require volume surge alongside OI drop — organic OI reduction with low volume is rejected | VERIFIED | `vol_surge_ratio < p.vol_surge_min_ratio` guard at line 206-207; `store.bar_volumes` used at line 199 |
| 4 | Tier and vol_surge_ratio are included in signal metadata for downstream consumers | VERIFIED | `metadata={"tier": tier, ..., "vol_surge_ratio": round(vol_surge_ratio, 3), ...}` at lines 273-281 |
| 5 | Correlation strategy computes basis z-scores at three lookback windows (short=30, medium=60, long=120) | VERIFIED | `z_short`, `z_medium`, `z_long` computed via `_compute_zscore()` at lines 144-146; params `basis_short_lookback=30`, `basis_medium_lookback=60`, `basis_long_lookback=120` |
| 6 | When 2 of 3 windows agree on direction, signal fires only if funding rate confirms the same direction | VERIFIED | `if agreements == 2: if not funding_confirms: ... return []` at lines 187-194 |
| 7 | When all 3 windows agree, signal fires regardless of funding rate direction | VERIFIED | `if agreements == 3: pass  # D-06: fire regardless of funding` at line 185-186 |
| 8 | Funding rate alignment boosts conviction by a configurable amount | VERIFIED | `conviction = min(conviction + p.funding_rate_boost, 1.0)` at line 215; `funding_rate_boost: 0.10` in YAML |
| 9 | High-conviction correlation signals route to Portfolio A | VERIFIED | `suggested_target = PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction else PortfolioTarget.B` at lines 222-226; threshold=0.70 |
| 10 | ADX threshold scales with volatility percentile — lower in low-vol, higher in high-vol | VERIFIED | `_compute_adaptive_thresholds()` at line 424; linear interpolation `adx_mult = low_mult + (high_mult - low_mult) * vol_pct` at line 441 |
| 11 | ATR expansion threshold scales with volatility percentile in the same direction, clamped to bounds | VERIFIED | Same `_compute_adaptive_thresholds()` method; ADX clamped `[15.0, 35.0]`, ATR expansion clamped `[0.8, 1.5]` at lines 443/450 |
| 12 | Signals emit trailing stop metadata and initial stop is tighter when trail metadata is enabled | VERIFIED | `base_metadata` includes `trail_enabled`, `trail_activation_pct`, `trail_distance_atr` at lines 340-344; `sl_mult_b = Decimal(str(p.initial_stop_atr_mult))` (1.8 ATR) when trail enabled at lines 351-354 |

**Score:** 12/12 truths verified

---

## Required Artifacts

### Plan 03-01 (Liquidation Cascade — LIQ-01, LIQ-02)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `agents/signals/strategies/liquidation_cascade.py` | Tiered cascade response with volume surge confirmation | VERIFIED | Contains `tier1_stop_atr_mult`, `_classify_tier`, `store.bar_volumes`, `"tier":`, `"vol_surge_ratio":` |
| `agents/signals/tests/test_liquidation_cascade.py` | Tests for tier classification, volume surge, conviction scaling | VERIFIED | 24 tests total; `TestTierClassification` (6 tests), `TestVolumeSurgeConfirmation` (4 tests) |
| `configs/strategies/liquidation_cascade.yaml` | Tier-specific and volume surge YAML config params | VERIFIED | Contains `tier1_stop_atr_mult: 1.5`, `vol_surge_min_ratio: 1.5`, SOL-PERP wider stops |

### Plan 03-02 (Correlation — CORR-01, CORR-02, CORR-03)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `agents/signals/strategies/correlation.py` | Multi-window basis analysis, funding integration, Portfolio A routing | VERIFIED | Contains `basis_short_lookback`, `store.funding_rates`, `PortfolioTarget.A`, `"windows_agreed":`, `"funding_confirms":` |
| `agents/signals/tests/test_correlation.py` | Tests for multi-window agreement, funding confirmation, Portfolio A routing | VERIFIED | 35 tests total; `TestMultiWindowBasis` (5), `TestFundingRateIntegration` (3), `TestPortfolioARouting` (2) |
| `configs/strategies/correlation.yaml` | Multi-window and funding YAML params | VERIFIED | Contains `basis_short_lookback: 30`, `basis_medium_lookback: 60`, `basis_long_lookback: 120`, `funding_rate_boost: 0.10`, `portfolio_a_min_conviction: 0.70`; no standalone `basis_lookback:` key |

### Plan 03-03 (Regime Trend — RT-01, RT-02)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `agents/signals/strategies/regime_trend.py` | Adaptive ADX/ATR thresholds and trailing stop metadata | VERIFIED | Contains `from scipy.stats import percentileofscore`, `adx_adapt_low_mult`, `adx_adapt_high_mult`, `_compute_adaptive_thresholds`, `trail_enabled`, `trail_activation_pct`, `trail_distance_atr`, `initial_stop_atr_mult` |
| `agents/signals/tests/test_regime_trend.py` | Tests for adaptive thresholds and trail metadata | VERIFIED | 24 tests total; `TestAdaptiveThresholds` (8 tests), `TestTrailingStopMetadata` (3 tests) |
| `configs/strategies/regime_trend.yaml` | Adaptive threshold and trail YAML params | VERIFIED | Contains `adx_adapt_enabled: true`, `adx_adapt_low_mult: 0.8`, `adx_adapt_high_mult: 1.2`, `trail_enabled: true`, `initial_stop_atr_mult: 1.8`; SOL/BTC/QQQ/SPY per-instrument overrides present |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `liquidation_cascade.py` | `feature_store.py` | `store.bar_volumes` for volume surge | WIRED | `bar_vols = store.bar_volumes` at line 199; used in surge ratio computation |
| `liquidation_cascade.py` | `liquidation_cascade.yaml` | `tier[123]_` params via YAML config loader | WIRED | All 6 tier stop/TP params loaded in `__init__` config branch; confirmed in YAML |
| `correlation.py` | `feature_store.py` | `store.funding_rates` for funding rate integration | WIRED | `funding_rates = store.funding_rates` at line 170; `cur_funding = float(funding_rates[-1])` at line 173 |
| `correlation.py` | `libs/common/models/enums.py` | `PortfolioTarget.A` for high-conviction routing | WIRED | `PortfolioTarget.A` at line 224; `PortfolioTarget.B` fallback; full routing decision at lines 222-226 |
| `regime_trend.py` | `scipy.stats` | `percentileofscore` for volatility percentile | WIRED | `from scipy.stats import percentileofscore` at line 27; called at line 238 and line 438 |
| `regime_trend.py` | `libs/common/models/signal.py` | `trail_` metadata in `StandardSignal.metadata` dict | WIRED | `trail_enabled`, `trail_activation_pct`, `trail_distance_atr` in `base_metadata` at lines 340-344 |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| LIQ-01 | 03-01-PLAN.md | Graduated response levels — Tier 1 (2-4%), Tier 2 (4-8%), Tier 3 (>8%) with different position sizing and stop widths | SATISFIED | `_classify_tier()`, tier-specific stop/TP multipliers, tier_boost conviction model all present and tested |
| LIQ-02 | 03-01-PLAN.md | Volume surge confirmation — require volume spike alongside OI drop to distinguish forced liquidation from organic OI reduction | SATISFIED | `vol_surge_ratio < p.vol_surge_min_ratio` gate at line 206; `store.bar_volumes` pattern; 4 dedicated tests pass |
| CORR-01 | 03-02-PLAN.md | Multi-window basis analysis — short (30), medium (60), long (120) bar lookback windows; signal fires when multiple agree | SATISFIED | Three z-scores computed; `agreements >= 2` gate enforced; `min_history` uses `basis_long_lookback` |
| CORR-02 | 03-02-PLAN.md | Funding rate integration — extreme funding + extreme basis = higher conviction; three-factor model | SATISFIED | `store.funding_rates` read, funding direction mapped to `funding_confirms`, conviction boosted by `funding_rate_boost=0.10` |
| CORR-03 | 03-02-PLAN.md | Portfolio A dual routing — multi-window + funding agreement signals eligible for autonomous execution | SATISFIED | `PortfolioTarget.A` when `conviction >= portfolio_a_min_conviction (0.70)` |
| RT-01 | 03-03-PLAN.md | Adaptive filter thresholds — ADX and ATR expansion thresholds adjust with volatility regime | SATISFIED | `_compute_adaptive_thresholds()` using `percentileofscore`; linear interpolation; clamping at bounds; `adx_adapt_enabled` toggle |
| RT-02 | 03-03-PLAN.md | Dynamic trailing stop concept — emit tighter initial stop with metadata suggesting trail parameters for execution layer | SATISFIED | `trail_enabled/trail_activation_pct/trail_distance_atr` in metadata; `initial_stop_atr_mult=1.8` ATR vs 2.5 default when trail enabled |

All 7 requirements satisfied. No orphaned requirements detected — all IDs appear in plan frontmatter and are tracked as Complete in REQUIREMENTS.md.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | — | — | — |

No TODO/FIXME/placeholder comments found. All `return []` occurrences are legitimate early-exit guard clauses consistent with the `list[StandardSignal]` return contract of `SignalStrategy.evaluate()`.

---

## Test Results

| Test Suite | Tests | Result |
|------------|-------|--------|
| `test_liquidation_cascade.py` | 24 | 24 passed |
| `test_correlation.py` | 35 | 35 passed |
| `test_regime_trend.py` | 24 | 24 passed |
| Full `agents/signals/tests/` suite | 179 | 179 passed (no regressions) |

---

## Human Verification Required

None. All must-haves are verifiable programmatically through code inspection and test execution.

---

## Summary

Phase 03 fully achieves its stated goal. All three strategies now produce more nuanced signals:

- **Liquidation Cascade**: Single OI threshold replaced by 3-tier cascade classification (Tier 1/2/3) with graduated stop/TP widths. Volume surge gate filters organic OI reduction. Tier and vol_surge_ratio emitted in metadata.

- **Correlation**: Single-lookback basis analysis replaced by three-window consensus (30/60/120 bars). The 2-of-3 agreement rule gates on funding rate confirmation; 3-of-3 fires unconditionally. Funding rate alignment boosts conviction. High-conviction (>= 0.70) signals route to Portfolio A.

- **Regime Trend**: Fixed ADX and ATR expansion thresholds replaced by volatility-adaptive versions computed via `percentileofscore`, interpolated across a configurable multiplier range, and clamped at hard bounds. Trailing stop parameters emitted in signal metadata for future execution layer consumption. Initial stop tightened (1.8x ATR vs 2.5x) when trailing is enabled.

All 7 requirement IDs (LIQ-01, LIQ-02, CORR-01, CORR-02, CORR-03, RT-01, RT-02) are implemented, tested, and marked complete in REQUIREMENTS.md. 83 new/updated tests pass. Full 179-test signals suite passes with no regressions.

---

_Verified: 2026-03-22T10:30:00Z_
_Verifier: Claude (gsd-verifier)_
