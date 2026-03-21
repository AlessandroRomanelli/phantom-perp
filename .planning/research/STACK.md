# Technology Stack

**Project:** Phantom Perp — Strategy Enhancement
**Researched:** 2026-03-21

## Context

The system already runs Python 3.13 with asyncio, numpy, polars, and hand-rolled indicators in `libs/indicators/`. Strategies operate on numpy arrays from a FeatureStore (deque-based rolling buffer, 500 samples at 60s intervals). TA-Lib is listed as a dependency but never actually imported by any strategy — all indicators (EMA, SMA, RSI, ADX, ATR, Bollinger, MACD, Stochastic, OBV, VWAP) are implemented as pure numpy functions in `libs/indicators/`.

This research covers **additional libraries needed** for the strategy enhancement milestone. The core stack (httpx, websockets, redis, pydantic, polars, structlog, etc.) is unchanged.

## Recommended Stack Additions

### Statistical Computation
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `scipy` | >=1.17,<2 | Z-scores, statistical tests, distributions | `scipy.stats.zscore` is the standard for z-score computation. Rolling z-scores for mean reversion and funding arb need proper statistical foundations. Also provides `percentileofscore`, `norm.cdf`, and distribution fitting for conviction models. Already battle-tested in quant finance. | HIGH |
| `bottleneck` | >=1.6,<2 | Fast rolling mean, rolling std, NaN-aware operations | C-compiled rolling window functions that are 100-6000x faster than pure numpy loops for rolling operations. Critical for rolling z-scores, rolling correlations, and adaptive parameter windows. `move_mean`, `move_std`, `move_rank` are exactly what the new strategies need. Minimal dependency (just numpy). | HIGH |

### Technical Indicators (Incremental)
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Do NOT add a TA library** | — | — | The codebase already has clean, well-tested numpy indicator implementations in `libs/indicators/`. Adding pandas-ta, talipp, or polars-ta would create two competing indicator stacks. The existing implementations are correct (Wilder smoothing, proper EMA seeding) and operate on the same numpy arrays the FeatureStore produces. New indicators (volume profile, Keltner channels, etc.) should be added to `libs/indicators/` following the same pattern. | HIGH |

### Performance Optimization
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `numba` | >=0.60,<1 | JIT compilation for hot-path indicator loops | The existing indicator code has Python `for` loops (ADX, RSI, Stochastic all loop element-by-element). Numba's `@njit` decorator can make these 10-100x faster with zero API changes. Critical for when per-instrument computation multiplies the workload by 5x. Add `@njit` to existing functions incrementally — no rewrite needed. | MEDIUM |

### No New Libraries Needed For
| Capability | How to Achieve | Rationale |
|------------|---------------|-----------|
| VWAP computation | Already implemented in `libs/indicators/volume.py` | Existing `vwap()` function computes cumulative VWAP from HLC + volume arrays. Extend with session-reset logic and VWAP bands (std dev bands around VWAP). |
| Volume profile | Hand-roll in `libs/indicators/volume.py` | Volume profile is just a histogram of volume by price level — `numpy.histogram` or `numpy.digitize` + `numpy.bincount`. No library needed for this; it's ~20 lines of numpy. |
| Orderbook imbalance | Hand-roll in a new `libs/indicators/orderbook.py` | FeatureStore already tracks `orderbook_imbalances`. Bid/ask ratio, depth-weighted imbalance, and absorption detection are straightforward numpy computations on the existing data. No external library provides value here — they all assume raw L2/L3 data, which this system has already pre-processed. |
| Rolling correlations | `numpy.corrcoef` or `bottleneck` + manual | Rolling correlation between price series, OI, and funding rates. Bottleneck's `move_mean`/`move_std` make Pearson correlation trivial to compute in rolling windows. |
| Adaptive parameters | `numpy` + `scipy` | Volatility regime detection uses ATR percentiles and ADX ranges — both computable with existing numpy + new scipy additions. |

## Detailed Rationale

### Why scipy (HIGH confidence)

The new strategies need proper statistical tools:

- **Funding arb**: Z-score of funding rate vs historical distribution to detect dislocations. `scipy.stats.zscore` handles this correctly with ddof parameter.
- **Mean reversion improvement**: Dynamic Bollinger band width based on percentile rank of current volatility. `scipy.stats.percentileofscore` is the clean way to do this.
- **Cross-strategy conviction**: Normalizing conviction scores across strategies with different scales. Z-score normalization is the standard approach.
- **Regime detection**: Kolmogorov-Smirnov test (`scipy.stats.kstest`) to detect distribution shifts in returns for regime change detection.

scipy is a ~30MB dependency but it's the undisputed standard for scientific Python. No lighter alternative covers this breadth.

### Why bottleneck (HIGH confidence)

The existing indicator code recomputes entire arrays on every FeatureStore update. With 5 instruments and 10+ strategies, rolling window operations become a hot path. Bottleneck provides:

- `move_mean(arr, window)` — rolling mean, 100x+ faster than numpy loop
- `move_std(arr, window)` — rolling std, needed for z-scores and Bollinger bands
- `move_rank(arr, window)` — rolling percentile rank, useful for regime detection
- `move_min/move_max(arr, window)` — rolling extremes for Keltner channels

All functions are NaN-aware and operate on numpy arrays (no pandas dependency). They drop directly into the existing `libs/indicators/` pattern.

### Why numba (MEDIUM confidence)

MEDIUM because the system currently runs fine without it — 500-sample arrays at 60s intervals are not computationally heavy. But with 5 instruments, each running 10 strategies with multiple indicators, the per-tick compute budget increases 5-10x. Numba provides insurance:

- Decorate existing loop-based functions with `@njit` for 10-100x speedup
- No API changes — same numpy arrays in, same arrays out
- First-call compilation overhead (~1-2s) is amortized over 24/7 operation
- Can be added incrementally to the hottest functions first (ADX, RSI are the loopiest)

The risk: numba adds ~200MB to the Docker image and has LLVM compilation overhead on first run. On the Oracle Always Free instance (4GB swap), this could be tight. **Defer if memory is a constraint** — profile first, optimize later.

### Why NOT pandas-ta / talipp / polars-ta

- **pandas-ta**: Requires pandas DataFrames. The system uses numpy arrays from deques. Converting to/from DataFrames adds overhead and complexity. Also, pandas-ta has 150+ indicators — the system needs maybe 5 new ones.
- **talipp**: Incremental O(1) updates sound appealing, but the FeatureStore recomputes indicators on the full 500-sample buffer each cycle. Switching to incremental would require refactoring the FeatureStore pattern. Not worth the architectural disruption.
- **polars-ta / polars-talib**: Polars is used for data processing elsewhere, but strategies operate on numpy arrays from FeatureStore deques. Adding a polars conversion step to each strategy adds complexity for marginal benefit.
- **ta-lib (already in pyproject.toml)**: Never actually imported by any strategy code. The pure-numpy implementations in `libs/indicators/` are sufficient and avoid the C library compilation headaches in Docker. **Consider removing ta-lib from dependencies** to simplify the Docker build.

### Why NOT scikit-learn / xgboost for this milestone

Already in `pyproject.toml` but explicitly out of scope for this milestone per PROJECT.md: "Machine learning / adaptive parameter optimization -- stick to proven quant patterns for now." These dependencies could be removed if unused, or kept for a future milestone.

## Installation

```bash
# New dependencies for strategy enhancement
pip install "scipy>=1.17,<2" "bottleneck>=1.6,<2"

# Optional — add only if profiling shows compute bottleneck
pip install "numba>=0.60,<1"
```

### pyproject.toml changes

```toml
dependencies = [
    # ... existing ...
    "scipy>=1.17,<2",
    "bottleneck>=1.6,<2",
    # Remove if confirmed unused:
    # "ta-lib>=0.4,<1",      # never imported by strategy code
    # "scikit-learn>=1.4,<2", # ML out of scope this milestone
    # "xgboost>=2,<3",        # ML out of scope this milestone
]

[project.optional-dependencies]
ml = [
    "scikit-learn>=1.4,<2",
    "xgboost>=2,<3",
]
perf = [
    "numba>=0.60,<1",
]
```

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Rolling stats | bottleneck | numpy sliding_window_view | sliding_window_view creates views (memory efficient) but still needs manual aggregation; bottleneck's C functions are faster end-to-end |
| Rolling stats | bottleneck | polars rolling expressions | Would require converting FeatureStore deques to polars Series; adds unnecessary conversion overhead |
| Z-scores | scipy.stats | Manual numpy `(x - mean) / std` | Works for simple cases but scipy handles edge cases (zero std, NaN, ddof), distribution tests, and percentiles that manual code would need to reimplement |
| Indicators | Hand-roll in libs/indicators | pandas-ta | 150+ indicators for 5 new ones is bloat; requires pandas conversion; competing with existing numpy pattern |
| Indicators | Hand-roll in libs/indicators | talipp (incremental) | Would require FeatureStore refactor to incremental pattern; current batch recompute on 500 samples is fast enough |
| JIT compilation | numba (deferred) | cython | Cython requires separate .pyx files and build step; numba is decorator-based with zero build changes |
| Volume profile | numpy.histogram | No library exists | Volume profile is a trivial histogram computation; no dedicated library needed |
| Orderbook analysis | Hand-roll numpy | cryptofeed / hft-orderbook | System already has pre-processed orderbook data in FeatureStore; external libraries assume raw L2/L3 feeds |

## Docker Impact

| Addition | Image Size Impact | Build Complexity |
|----------|-------------------|-----------------|
| scipy | +30MB | None (pip install, wheels available) |
| bottleneck | +2MB | None (pip install, wheels available) |
| numba (optional) | +200MB | Moderate (LLVM dependency) |
| Remove ta-lib | -50MB | Removes C library compilation step from Dockerfile |

**Net recommendation**: Add scipy + bottleneck, remove ta-lib = roughly net zero on image size, simpler build.

## Sources

- [SciPy zscore documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.zscore.html) — current API reference
- [Bottleneck PyPI](https://pypi.org/project/Bottleneck/) — v1.6.0, Sep 2025
- [Bottleneck GitHub](https://github.com/pydata/bottleneck) — benchmark data for rolling operations
- [talipp PyPI](https://pypi.org/project/talipp/) — v2.7.0, evaluated and rejected
- [pandas-ta PyPI](https://pypi.org/project/pandas-ta/) — evaluated and rejected
- [numba documentation](https://numba.readthedocs.io/en/stable/user/performance-tips.html) — JIT compilation performance tips
- [Polars rolling expressions](https://docs.pola.rs/api/python/dev/reference/expressions/api/polars.Expr.rolling_mean.html) — evaluated for indicator computation
