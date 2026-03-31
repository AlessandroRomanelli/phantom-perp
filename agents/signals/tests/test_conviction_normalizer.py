"""Tests for conviction normalizer utility."""

from __future__ import annotations

from agents.signals.conviction_normalizer import (
    ROUTE_A_UNIFIED_THRESHOLD,
    NormalizedConviction,
    normalize_conviction,
    should_route_a,
)


def test_high_band_above_threshold() -> None:
    """Conviction 0.85 should map to high band."""
    result = normalize_conviction(0.85)
    assert isinstance(result, NormalizedConviction)
    assert result.band == "high"


def test_high_band_boundary_inclusive() -> None:
    """Conviction 0.70 should map to high band (boundary inclusive)."""
    result = normalize_conviction(0.70)
    assert result.band == "high"


def test_medium_band() -> None:
    """Conviction 0.60 should map to medium band."""
    result = normalize_conviction(0.60)
    assert result.band == "medium"


def test_medium_band_boundary_inclusive() -> None:
    """Conviction 0.50 should map to medium band (boundary inclusive)."""
    result = normalize_conviction(0.50)
    assert result.band == "medium"


def test_low_band() -> None:
    """Conviction 0.40 should map to low band."""
    result = normalize_conviction(0.40)
    assert result.band == "low"


def test_low_band_floor() -> None:
    """Conviction 0.30 should map to low band."""
    result = normalize_conviction(0.30)
    assert result.band == "low"


def test_preserves_raw_conviction() -> None:
    """raw_conviction should be preserved unchanged."""
    result = normalize_conviction(0.73)
    assert result.raw_conviction == 0.73
    assert result.normalized_conviction == 0.73  # Identity mapping


def test_portfolio_a_threshold_value() -> None:
    """ROUTE_A_UNIFIED_THRESHOLD should be 0.70."""
    assert ROUTE_A_UNIFIED_THRESHOLD == 0.70


def test_should_route_a_above() -> None:
    """Conviction >= 0.70 should route to Portfolio A."""
    assert should_route_a(0.85) is True
    assert should_route_a(0.70) is True


def test_should_route_a_below() -> None:
    """Conviction < 0.70 should not route to Portfolio A."""
    assert should_route_a(0.69) is False
    assert should_route_a(0.50) is False


def test_frozen_dataclass() -> None:
    """NormalizedConviction should be immutable."""
    result = normalize_conviction(0.60)
    try:
        result.band = "high"  # type: ignore[misc]
        raise AssertionError("Should not allow mutation")
    except AttributeError:
        pass
