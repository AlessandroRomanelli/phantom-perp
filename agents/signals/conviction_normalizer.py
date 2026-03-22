"""Post-processing conviction band mapping.

Maps raw conviction values to named bands (high/medium/low) for
consistent cross-strategy conviction interpretation and unified
Portfolio A routing decisions.

Follows the established function-based utility pattern from funding_filter.py:
frozen dataclass result, no class state, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass

# Unified Portfolio A routing threshold (D-07)
PORTFOLIO_A_UNIFIED_THRESHOLD: float = 0.70


@dataclass(frozen=True, slots=True)
class NormalizedConviction:
    """Result of conviction normalization."""

    raw_conviction: float
    normalized_conviction: float
    band: str


def normalize_conviction(raw: float) -> NormalizedConviction:
    """Map a raw conviction value to a named band.

    Bands (per D-06):
    - "high": conviction >= 0.70
    - "medium": conviction >= 0.50
    - "low": conviction < 0.50 (floor ~0.30 from min_conviction thresholds)

    The normalized_conviction is an identity mapping (equals raw) per D-05.
    This is a post-processing overlay, not a value rewrite.

    Args:
        raw: Raw conviction value from strategy [0.0, 1.0].

    Returns:
        NormalizedConviction with raw_conviction, normalized_conviction, and band.
    """
    if raw >= 0.70:
        band = "high"
    elif raw >= 0.50:
        band = "medium"
    else:
        band = "low"

    return NormalizedConviction(
        raw_conviction=raw,
        normalized_conviction=raw,
        band=band,
    )


def should_route_portfolio_a(conviction: float) -> bool:
    """Check if conviction meets the unified Portfolio A threshold.

    Args:
        conviction: Conviction value to check.

    Returns:
        True if conviction >= PORTFOLIO_A_UNIFIED_THRESHOLD.
    """
    return conviction >= PORTFOLIO_A_UNIFIED_THRESHOLD
