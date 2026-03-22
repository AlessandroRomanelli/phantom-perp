"""Tests for the funding rate filter utility."""

import numpy as np
import pytest

from libs.common.models.enums import PositionSide

from agents.signals.funding_filter import FundingBoostResult, compute_funding_boost


class TestFundingBoost:
    """Tests for compute_funding_boost function."""

    def test_boost_long_negative_funding(self) -> None:
        """Negative funding (shorts pay longs) confirms LONG direction."""
        rates = np.array([0.0001] * 49 + [-0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.LONG,
            hours_since_last_funding=0.9,
        )
        assert result.aligned is True
        assert result.boost > 0.0

    def test_boost_short_positive_funding(self) -> None:
        """Positive funding (longs pay shorts) confirms SHORT direction."""
        rates = np.array([-0.0001] * 49 + [0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.9,
        )
        assert result.aligned is True
        assert result.boost > 0.0

    def test_no_boost_opposing(self) -> None:
        """Positive funding opposes LONG direction -- boost must be 0 (never suppress)."""
        rates = np.array([-0.0001] * 49 + [0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.LONG,
            hours_since_last_funding=0.9,
        )
        assert result.aligned is False
        assert result.boost == 0.0

    def test_sparse_data_guard(self) -> None:
        """Fewer than min_samples entries returns boost=0 regardless of alignment."""
        # Only 5 entries, default min_samples=10
        rates = np.array([-0.0005] * 5, dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.LONG,
            hours_since_last_funding=0.9,
        )
        assert result.boost == 0.0
        assert result.aligned is False

    def test_zscore_computation(self) -> None:
        """Known funding_rates array produces expected z-score above threshold."""
        rates = np.array([0.0001] * 49 + [0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.95,
        )
        # The last value is significantly higher than the mean, z-score should be > 1.5
        assert result.z_score > 1.5

    def test_zscore_zero_std(self) -> None:
        """All identical funding rates produces z_score=0.0."""
        rates = np.array([0.0001] * 50, dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.5,
        )
        assert result.z_score == 0.0
        assert result.boost == 0.0

    def test_settlement_decay_near_settlement(self) -> None:
        """hours_since_last_funding=0.95 produces decay_factor close to 1.0."""
        rates = np.array([0.0001] * 49 + [0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.95,
        )
        # exp(-2 * (1 - 0.95)) = exp(-0.1) ~ 0.905
        assert result.decay_factor > 0.85

    def test_settlement_decay_just_settled(self) -> None:
        """hours_since_last_funding=0.0 produces decay_factor close to exp(-2) ~ 0.135."""
        rates = np.array([0.0001] * 49 + [0.0005], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.0,
        )
        # exp(-2 * (1 - 0.0)) = exp(-2) ~ 0.1353
        assert abs(result.decay_factor - 0.1353) < 0.01

    def test_max_boost_capping(self) -> None:
        """Even with extreme z-score and perfect decay, boost does not exceed max_boost."""
        # Create very extreme z-score
        rates = np.array([0.0001] * 49 + [0.01], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.SHORT,
            hours_since_last_funding=0.99,
            max_boost=0.10,
        )
        assert result.boost <= 0.10

    def test_empty_funding_rates(self) -> None:
        """Empty array returns boost=0.0 and z_score=0.0."""
        rates = np.array([], dtype=np.float64)
        result = compute_funding_boost(
            funding_rates=rates,
            signal_direction=PositionSide.LONG,
            hours_since_last_funding=0.5,
        )
        assert result.boost == 0.0
        assert result.z_score == 0.0
        assert result.aligned is False
