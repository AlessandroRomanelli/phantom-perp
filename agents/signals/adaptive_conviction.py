"""Shared volatility-percentile conviction threshold scaling.

Scales a base conviction threshold down in low-volatility environments
(to fire more signals when markets are quiet) and up in high-volatility
environments (to require higher conviction when noise is elevated).

Follows the established function-based utility pattern from funding_filter.py:
frozen dataclass result, no class state, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.stats import percentileofscore


@dataclass(frozen=True, slots=True)
class AdaptiveConvictionResult:
    """Result of adaptive conviction threshold computation."""

    adjusted_threshold: float
    volatility_percentile: float


def compute_adaptive_threshold(
    atr_vals: NDArray[np.float64],
    cur_atr: float,
    base_threshold: float,
    low_vol_mult: float = 0.7,
    high_vol_mult: float = 1.2,
    min_samples: int = 20,
) -> AdaptiveConvictionResult:
    """Scale a base threshold by ATR volatility percentile.

    Args:
        atr_vals: Historical ATR values array.
        cur_atr: Current ATR value to rank against history.
        base_threshold: Base conviction threshold to scale.
        low_vol_mult: Multiplier at 0th percentile (lowers threshold).
        high_vol_mult: Multiplier at 100th percentile (raises threshold).
        min_samples: Minimum valid ATR entries required to compute.

    Returns:
        AdaptiveConvictionResult with adjusted_threshold and volatility_percentile.
    """
    # Filter NaN values
    valid_atr = atr_vals[~np.isnan(atr_vals)]

    if len(valid_atr) < min_samples:
        return AdaptiveConvictionResult(
            adjusted_threshold=base_threshold,
            volatility_percentile=0.5,
        )

    vol_pct = float(percentileofscore(valid_atr, cur_atr)) / 100.0
    mult = low_vol_mult + (high_vol_mult - low_vol_mult) * vol_pct
    adjusted = round(base_threshold * mult, 4)

    return AdaptiveConvictionResult(
        adjusted_threshold=adjusted,
        volatility_percentile=round(vol_pct, 4),
    )
