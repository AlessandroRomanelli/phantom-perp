---
phase: 02-momentum-mean-reversion
verified: 2026-03-22T09:35:17Z
status: passed
score: 12/12 must-haves verified
re_verification: false
---

# Phase 02: Momentum and Mean Reversion Verification Report

**Phase Goal:** The two highest-frequency existing strategies produce higher-quality signals with fewer false positives and can route high-conviction signals to Portfolio A
**Verified:** 2026-03-22T09:35:17Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Momentum rejects EMA crossovers when bar volume is below 50% of rolling average | VERIFIED | `momentum.py:192-199` — `bar_vols = store.bar_volumes`, rejects when `cur_vol < vol_avg * p.vol_min_ratio` |
| 2 | Momentum conviction scales with volatility percentile (high-vol breakouts score higher) | VERIFIED | `momentum.py:296-338` — 3-component model: ADX (0-0.35) + RSI (0-0.35) + vol/volatility (0-0.30) using `percentileofscore` |
| 3 | Momentum stops placed at swing high/low with ATR fallback when no swing found | VERIFIED | `momentum.py:228-244` — `_find_swing_low`/`_find_swing_high` called; ATR fallback explicit when swing is None |
| 4 | High-conviction momentum signals (>= 0.75) route to Portfolio A | VERIFIED | `momentum.py:247-251` — `PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction` (default 0.75) |
| 5 | All MomentumParams fields are loaded from YAML config (D-07 fix) | VERIFIED | `momentum.py:84-106` — 17 p.get() calls covering all fields; grep count = 17 |
| 6 | Momentum enabled for all 5 instruments with weight 0.20 (D-05, D-06) | VERIFIED | `momentum.yaml:3-4` — `enabled: true`, `weight: 0.20`; `strategy_matrix.yaml:7-14` — all 5 perps listed |
| 7 | Mean reversion rejects signals using multi-factor trend strength (EMA slope + consecutive closes + ADX) | VERIFIED | `mean_reversion.py:122-153` — `_compute_trend_strength` with 3 weighted components; called at line 205 |
| 8 | Bollinger Band width adapts to volatility regime (tighter in low-vol, wider in high-vol) | VERIFIED | `mean_reversion.py:182-189` — `adaptive_std = p.bb_std * (0.8 + 0.4 * vol_pct)` using ATR percentile |
| 9 | Strong reversions (deviation > threshold) get extended take-profit beyond the middle band with partial target in metadata | VERIFIED | `mean_reversion.py:277-291` — `if deviation > p.extended_deviation_threshold`; `partial_target` in metadata at line 339 |
| 10 | Extreme deviation signals with high conviction (>= 0.65) route to Portfolio A | VERIFIED | `mean_reversion.py:299-303` — `PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction` (default 0.65) |
| 11 | Mean reversion YAML loader loads all fields including atr_period, stop_loss_atr_mult, cooldown_bars | VERIFIED | `mean_reversion.py:82-105` — 15 p.get() calls covering previously-missing atr_period, stop_loss_atr_mult, cooldown_bars plus 4 new Phase 2 fields |
| 12 | Volume confirmation boosts conviction when bar volume is high on band touch (D-15) | VERIFIED | `mean_reversion.py:250-264` — `store.bar_volumes` used; `volume_ratio` passed to `_compute_conviction` which adds vol_score (0-0.25) |

**Score:** 12/12 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `agents/signals/strategies/momentum.py` | Volume filter, adaptive conviction, swing stops, Portfolio A routing | VERIFIED | All 4 mechanisms implemented; contains `portfolio_a_min_conviction` field and routing |
| `configs/strategies/momentum.yaml` | Momentum config with all params, weight 0.20, enabled | VERIFIED | `enabled: true`, `weight: 0.20`, all 17 params present |
| `configs/strategy_matrix.yaml` | Momentum enabled for all instruments | VERIFIED | `momentum: enabled: true` with ETH-PERP, BTC-PERP, SOL-PERP, QQQ-PERP, SPY-PERP |
| `agents/signals/tests/test_momentum.py` | Tests for volume filter, adaptive conviction, swing stops, Portfolio A routing | VERIFIED | 21 tests across 6 classes: TestMomentumStrategy, TestMomentumConfig, TestMomentumVolumeFilter, TestMomentumAdaptiveConviction, TestMomentumSwingStops, TestMomentumPortfolioRouting; all pass |
| `agents/signals/strategies/mean_reversion.py` | Multi-factor trend rejection, adaptive bands, extended targets, volume boost, Portfolio A routing | VERIFIED | All mechanisms implemented; contains `portfolio_a_min_conviction` field and routing |
| `configs/strategies/mean_reversion.yaml` | New params for trend rejection, extended targets, Portfolio A threshold | VERIFIED | Contains `trend_reject_threshold: 0.6`, `extended_deviation_threshold: 0.5`, `portfolio_a_min_conviction: 0.65`, `vol_lookback: 10` |
| `agents/signals/tests/test_mean_reversion.py` | Tests for trend rejection, adaptive bands, extended targets, Portfolio A routing | VERIFIED | 29 tests across 8 classes including TestMRTrendRejection, TestMRAdaptiveBands, TestMRExtendedTargets, TestMRPortfolioRouting, TestMRVolumeBoost; all pass |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `momentum.py` | `feature_store.py` | `store.bar_volumes` for volume confirmation | WIRED | Line 192: `bar_vols = store.bar_volumes` — read and used in filter |
| `momentum.py` | `libs/common/models/enums.py` | `PortfolioTarget.A` for high-conviction routing | WIRED | Line 248: `PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction` |
| `mean_reversion.py` | `feature_store.py` | `store.bar_volumes` for volume conviction boost | WIRED | Line 250: `bar_vols = store.bar_volumes` — used to compute `volume_ratio` fed to `_compute_conviction` |
| `mean_reversion.py` | `libs/indicators/moving_averages.py` | `ema()` for trend slope computation | WIRED | Line 31 import: `from libs.indicators.moving_averages import ema`; used in `_compute_trend_strength` at line 135 |
| `mean_reversion.py` | `libs/common/models/enums.py` | `PortfolioTarget.A` for high-conviction routing | WIRED | Line 300: `PortfolioTarget.A if conviction >= p.portfolio_a_min_conviction` |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| MOM-01 | 02-01-PLAN.md | Volume confirmation — reject EMA crossovers when volume is declining | SATISFIED | `momentum.py:191-200` — volume gate before conviction, rejects when `cur_vol < vol_avg * 0.5` |
| MOM-02 | 02-01-PLAN.md | Adaptive conviction model — scale with current vs historical volatility percentile | SATISFIED | `momentum.py:296-338` — `_compute_conviction` uses `percentileofscore(valid_atr, cur_atr)` for ATR percentile component |
| MOM-03 | 02-01-PLAN.md | Structure-aware stop placement — use recent swing high/low instead of fixed ATR multiples | SATISFIED | `momentum.py:340-422` — `_find_swing_low` and `_find_swing_high` methods with ATR fallback |
| MOM-04 | 02-01-PLAN.md | Portfolio A dual routing — high-conviction breakout signals eligible for autonomous execution | SATISFIED | `momentum.py:246-251` — signals with conviction >= 0.75 get `suggested_target=PortfolioTarget.A` |
| MR-01 | 02-02-PLAN.md | Multi-factor trend rejection — EMA slope + consecutive closes + momentum strength | SATISFIED | `mean_reversion.py:122-153` — `_compute_trend_strength` composite score with 3 weighted factors |
| MR-02 | 02-02-PLAN.md | Adaptive band width — adjust Bollinger Band std multiplier based on volatility regime | SATISFIED | `mean_reversion.py:182-189` — `adaptive_std = p.bb_std * (0.8 + 0.4 * vol_pct)` |
| MR-03 | 02-02-PLAN.md | Improved take-profit targeting — partial targets at mean, extended targets beyond for strong reversions | SATISFIED | `mean_reversion.py:277-291` — extended TP formula with `partial_target` metadata when deviation > threshold |
| MR-04 | 02-02-PLAN.md | Portfolio A dual routing — extreme deviation signals eligible for autonomous execution | SATISFIED | `mean_reversion.py:298-303` — signals with conviction >= 0.65 get `suggested_target=PortfolioTarget.A` |

All 8 requirement IDs from both plan frontmatter declarations accounted for. No orphaned requirements — REQUIREMENTS.md confirms all 8 map to Phase 2 and marks them complete.

---

### Anti-Patterns Found

No blocker or warning anti-patterns detected.

| File | Pattern | Severity | Verdict |
|------|---------|----------|---------|
| `momentum.py:167-179` | Debug `_log.info` call inside `evaluate()` every 10 bars | Info | Operational debug logging, not a stub. Does not affect correctness. |
| `mean_reversion.py:209-223` | Debug `_log.info` call inside `evaluate()` every 10 bars | Info | Same pattern as momentum. Production-acceptable instrumentation. |
| All `return []` occurrences (15 across both files) | Multiple `return []` in evaluate() | Info | All are legitimate filter/guard exits (min_history, cooldown, volume, trend, RSI checks), not stub returns. |

---

### Human Verification Required

None — all observable truths were verifiable via static analysis and test execution. The following would be validated in live paper trading but are not blockers for phase completion:

1. **Momentum signal frequency** — Whether volume filter reduces false signals at the expected rate in production market data.
   - Why human: Requires live or replay market data; cannot be determined from unit tests alone.

2. **Portfolio A routing rate in practice** — Whether conviction >= 0.75 (momentum) and >= 0.65 (mean reversion) thresholds produce a sensible mix of A/B routed signals.
   - Why human: Depends on real market volatility distribution; unit tests use synthetic data.

---

### Summary

Phase 02 fully achieves its goal. Both the momentum and mean reversion strategies now:

- **Filter false positives** via volume confirmation (momentum: hard gate; mean reversion: conviction boost) and trend rejection (mean reversion: composite EMA slope + consecutive closes + ADX score).
- **Produce higher-quality signals** through adaptive parameters: momentum conviction scales with ATR volatility percentile; mean reversion band width adapts to ATR regime; momentum stops use structural swing points with ATR fallback.
- **Route high-conviction signals to Portfolio A** at configurable conviction thresholds (0.75 for momentum, 0.65 for mean reversion) via `suggested_target=PortfolioTarget.A`.

All 8 requirements (MOM-01 through MOM-04, MR-01 through MR-04) are fully implemented, YAML-configured, and covered by passing test suites (21 momentum tests, 29 mean reversion tests).

---

_Verified: 2026-03-22T09:35:17Z_
_Verifier: Claude (gsd-verifier)_
