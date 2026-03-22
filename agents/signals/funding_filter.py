"""Shared funding rate confirmation utility for signal strategies.

Computes a conviction boost when funding rate alignment confirms a signal
direction. Positive funding (longs pay shorts) confirms SHORT signals;
negative funding (shorts pay longs) confirms LONG signals.

Boost-only semantics: opposing funding returns boost=0.0 (never suppresses).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

import numpy as np
from numpy.typing import NDArray

from libs.common.models.enums import PositionSide


@dataclass(frozen=True, slots=True)
class FundingBoostResult:
    """Result of funding rate confirmation analysis."""

    boost: float
    z_score: float
    decay_factor: float
    aligned: bool


def compute_funding_boost(
    funding_rates: NDArray[np.float64],
    signal_direction: PositionSide,
    hours_since_last_funding: float,
    z_score_threshold: float = 1.5,
    max_boost: float = 0.10,
    lookback: int = 50,
    min_samples: int = 10,
) -> FundingBoostResult:
    """Compute conviction boost from funding rate alignment.

    Args:
        funding_rates: Historical funding rate array (sparse, from FeatureStore).
        signal_direction: The signal's proposed direction (LONG or SHORT).
        hours_since_last_funding: Fractional hours since last funding settlement [0, 1].
        z_score_threshold: Minimum absolute z-score to trigger boost.
        max_boost: Maximum conviction boost value.
        lookback: Number of recent funding rates to use for z-score window.
        min_samples: Minimum funding rate entries required to compute.

    Returns:
        FundingBoostResult with boost, z_score, decay_factor, and alignment flag.
    """
    # Guard: insufficient data
    if len(funding_rates) < min_samples:
        return FundingBoostResult(boost=0.0, z_score=0.0, decay_factor=0.0, aligned=False)

    # Compute z-score over lookback window
    window = funding_rates[-lookback:]
    mean = float(np.mean(window))
    std = float(np.std(window, ddof=1))

    if std < 1e-12:
        z_score = 0.0
    else:
        z_score = (float(funding_rates[-1]) - mean) / std

    # Check alignment
    # Negative funding -> bullish (confirms LONG)
    # Positive funding -> bearish (confirms SHORT)
    cur_rate = float(funding_rates[-1])
    aligned = False
    if signal_direction == PositionSide.LONG and cur_rate < 0:
        aligned = True
    elif signal_direction == PositionSide.SHORT and cur_rate > 0:
        aligned = True

    if not aligned:
        return FundingBoostResult(
            boost=0.0,
            z_score=round(z_score, 4),
            decay_factor=0.0,
            aligned=False,
        )

    # Compute settlement decay: urgency increases as next settlement approaches
    clamped_hours = max(0.0, min(hours_since_last_funding, 1.0))
    decay_factor = exp(-2.0 * (1.0 - clamped_hours))

    # Compute boost if z-score exceeds threshold
    if abs(z_score) >= z_score_threshold:
        boost = max_boost * (abs(z_score) / z_score_threshold) * decay_factor
        boost = min(boost, max_boost)
    else:
        boost = 0.0

    return FundingBoostResult(
        boost=round(boost, 4),
        z_score=round(z_score, 4),
        decay_factor=round(decay_factor, 4),
        aligned=aligned,
    )
