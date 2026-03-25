"""Unit tests for libs/tuner/recommender.py.

Coverage:
- validate_recommendation: clips out-of-bounds value to max/min (D-09)
- validate_recommendation: preserves in-bounds value unchanged
- validate_recommendation: rejects unknown param name (D-10)
- validate_recommendation: rounds int-typed params after clipping (D-11)
- validate_recommendation: rounds int value that is already in bounds
- validate_recommendation: returns None on non-numeric value
- _group_recommendations: groups by strategy, separates base vs instrument changes
- run_tuning_cycle: calls apply_parameter_changes and log_parameter_change
- run_tuning_cycle: calls log_no_change when no recommendations (D-15)
- run_tuning_cycle: returns empty TuningResult on API failure (D-13)
- run_tuning_cycle: returns TuningResult with summary string (D-06)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from libs.tuner.audit import ParameterChange
from libs.tuner.bounds import BoundsEntry


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_float_entry(param: str, min_val: float, max_val: float) -> BoundsEntry:
    return BoundsEntry(param_name=param, min_value=min_val, max_value=max_val, value_type="float")


def _make_int_entry(param: str, min_val: float, max_val: float) -> BoundsEntry:
    return BoundsEntry(param_name=param, min_value=min_val, max_value=max_val, value_type="int")


def _make_change(
    strategy: str = "momentum",
    instrument: str | None = None,
    param: str = "min_conviction",
    old_value: float = 0.50,
    new_value: float = 0.65,
    reasoning: str = "",
) -> ParameterChange:
    return ParameterChange(
        strategy=strategy,
        instrument=instrument,
        param=param,
        old_value=old_value,
        new_value=new_value,
        reasoning=reasoning,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# validate_recommendation tests
# ---------------------------------------------------------------------------


def test_validate_recommendation_clips_to_max() -> None:
    """Value above max is clipped down to max (D-09)."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}
    rec = {
        "strategy": "momentum",
        "param": "min_conviction",
        "value": 1.5,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result == pytest.approx(0.90)


def test_validate_recommendation_clips_to_min() -> None:
    """Value below min is clipped up to min (D-09)."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}
    rec = {
        "strategy": "momentum",
        "param": "min_conviction",
        "value": 0.01,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result == pytest.approx(0.10)


def test_validate_recommendation_preserves_in_bounds_value() -> None:
    """Value within bounds is returned unchanged."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}
    rec = {
        "strategy": "momentum",
        "param": "min_conviction",
        "value": 0.55,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result == pytest.approx(0.55)


def test_validate_recommendation_rejects_unknown_param() -> None:
    """Unknown param name returns None (D-10)."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}
    rec = {
        "strategy": "momentum",
        "param": "unknown_param",
        "value": 0.5,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result is None


def test_validate_recommendation_rounds_int_type() -> None:
    """Float value for int-typed param is rounded to nearest int (D-11)."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"cooldown_bars": _make_int_entry("cooldown_bars", 1, 30)}
    rec = {
        "strategy": "momentum",
        "param": "cooldown_bars",
        "value": 7.6,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result == 8
    assert isinstance(result, int)


def test_validate_recommendation_rounds_int_after_clip() -> None:
    """Out-of-bounds float for int param is clipped then rounded (D-09 + D-11)."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"cooldown_bars": _make_int_entry("cooldown_bars", 1, 30)}
    rec = {
        "strategy": "momentum",
        "param": "cooldown_bars",
        "value": 35.7,
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result == 30
    assert isinstance(result, int)


def test_validate_recommendation_non_numeric_value() -> None:
    """Non-numeric value returns None."""
    from libs.tuner.recommender import validate_recommendation

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}
    rec = {
        "strategy": "momentum",
        "param": "min_conviction",
        "value": "not_a_number",
        "reasoning": "test",
    }
    result = validate_recommendation(rec, registry)
    assert result is None


# ---------------------------------------------------------------------------
# _group_recommendations tests
# ---------------------------------------------------------------------------


def test_group_recommendations_by_strategy() -> None:
    """Groups base and instrument recommendations into the correct buckets."""
    from libs.tuner.recommender import _group_recommendations

    registry = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
        "cooldown_bars": _make_int_entry("cooldown_bars", 1, 30),
        "weight": _make_float_entry("weight", 0.05, 0.50),
    }

    recs = [
        {
            "strategy": "momentum",
            "instrument": None,
            "param": "min_conviction",
            "value": 0.6,
            "reasoning": "low win rate",
        },
        {
            "strategy": "momentum",
            "instrument": "ETH-PERP",
            "param": "cooldown_bars",
            "value": 5,
            "reasoning": "too noisy",
        },
        {
            "strategy": "mean_reversion",
            "instrument": None,
            "param": "weight",
            "value": 0.15,
            "reasoning": "outperforming",
        },
    ]

    result = _group_recommendations(recs, registry)

    assert "momentum" in result
    assert "mean_reversion" in result

    # Base-level momentum change
    assert result["momentum"]["changes"]["min_conviction"] == pytest.approx(0.6)
    # Instrument-level momentum change
    assert result["momentum"]["instrument_changes"]["ETH-PERP"]["cooldown_bars"] == 5
    # mean_reversion base change
    assert result["mean_reversion"]["changes"]["weight"] == pytest.approx(0.15)
    # Empty instrument changes for mean_reversion
    assert result["mean_reversion"]["instrument_changes"] == {}


def test_group_recommendations_skips_invalid() -> None:
    """Invalid recommendations (unknown param, non-numeric) are skipped."""
    from libs.tuner.recommender import _group_recommendations

    registry = {"min_conviction": _make_float_entry("min_conviction", 0.10, 0.90)}

    recs = [
        {
            "strategy": "momentum",
            "instrument": None,
            "param": "unknown_param",
            "value": 0.5,
            "reasoning": "test",
        },
        {
            "strategy": "momentum",
            "instrument": None,
            "param": "min_conviction",
            "value": "bad",
            "reasoning": "test",
        },
    ]

    result = _group_recommendations(recs, registry)
    # Both recs are invalid -- strategy bucket should not be created
    assert result == {}


# ---------------------------------------------------------------------------
# run_tuning_cycle tests
# ---------------------------------------------------------------------------


@patch("libs.tuner.recommender.log_parameter_change")
@patch("libs.tuner.recommender.apply_parameter_changes")
@patch("libs.tuner.recommender.load_strategy_config")
@patch("libs.tuner.recommender.compute_strategy_metrics")
@patch("libs.tuner.recommender.load_bounds_registry")
@patch("libs.tuner.recommender.build_user_message")
@patch("libs.tuner.recommender.build_system_prompt")
@patch("libs.tuner.recommender.call_claude")
def test_run_tuning_cycle_calls_apply_and_logs(
    mock_call_claude: MagicMock,
    mock_build_system: MagicMock,
    mock_build_user: MagicMock,
    mock_load_bounds: MagicMock,
    mock_compute_metrics: MagicMock,
    mock_load_config: MagicMock,
    mock_apply: MagicMock,
    mock_log_change: MagicMock,
) -> None:
    """run_tuning_cycle calls apply_parameter_changes and log_parameter_change."""
    from libs.tuner.recommender import TuningResult, run_tuning_cycle

    # Set up mocks
    mock_call_claude.return_value = {
        "summary": "test summary",
        "recommendations": [
            {
                "strategy": "momentum",
                "instrument": None,
                "param": "min_conviction",
                "value": 0.65,
                "reasoning": "low win rate",
            }
        ],
    }
    mock_compute_metrics.return_value = {
        ("momentum", "ETH-PERP"): None,  # below gate
    }
    mock_load_config.return_value = {}
    mock_load_bounds.return_value = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
    }
    mock_build_system.return_value = "system prompt"
    mock_build_user.return_value = "user message"

    change = _make_change(param="min_conviction", reasoning="")
    mock_apply.return_value = [change]

    result = run_tuning_cycle(
        fills=[],
        config_dir=Path("/fake/configs"),
        bounds_path=Path("/fake/bounds.yaml"),
    )

    mock_apply.assert_called_once()
    mock_log_change.assert_called_once()


@patch("libs.tuner.recommender.log_no_change")
@patch("libs.tuner.recommender.apply_parameter_changes")
@patch("libs.tuner.recommender.load_strategy_config")
@patch("libs.tuner.recommender.compute_strategy_metrics")
@patch("libs.tuner.recommender.load_bounds_registry")
@patch("libs.tuner.recommender.build_user_message")
@patch("libs.tuner.recommender.build_system_prompt")
@patch("libs.tuner.recommender.call_claude")
def test_run_tuning_cycle_no_changes(
    mock_call_claude: MagicMock,
    mock_build_system: MagicMock,
    mock_build_user: MagicMock,
    mock_load_bounds: MagicMock,
    mock_compute_metrics: MagicMock,
    mock_load_config: MagicMock,
    mock_apply: MagicMock,
    mock_log_no_change: MagicMock,
) -> None:
    """Empty recommendations triggers log_no_change (D-15)."""
    from libs.tuner.recommender import TuningResult, run_tuning_cycle

    mock_call_claude.return_value = {
        "summary": "everything looks good",
        "recommendations": [],
    }
    mock_compute_metrics.return_value = {}
    mock_load_bounds.return_value = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
    }
    mock_build_system.return_value = "system"
    mock_build_user.return_value = "user"

    result = run_tuning_cycle(
        fills=[],
        config_dir=Path("/fake/configs"),
        bounds_path=Path("/fake/bounds.yaml"),
    )

    mock_apply.assert_not_called()
    mock_log_no_change.assert_called_once()
    assert isinstance(result, TuningResult)
    assert result.changes == []


@patch("libs.tuner.recommender.apply_parameter_changes")
@patch("libs.tuner.recommender.load_strategy_config")
@patch("libs.tuner.recommender.compute_strategy_metrics")
@patch("libs.tuner.recommender.load_bounds_registry")
@patch("libs.tuner.recommender.build_user_message")
@patch("libs.tuner.recommender.build_system_prompt")
@patch("libs.tuner.recommender.call_claude")
def test_run_tuning_cycle_api_failure(
    mock_call_claude: MagicMock,
    mock_build_system: MagicMock,
    mock_build_user: MagicMock,
    mock_load_bounds: MagicMock,
    mock_compute_metrics: MagicMock,
    mock_load_config: MagicMock,
    mock_apply: MagicMock,
) -> None:
    """API failure (None response) returns empty TuningResult, no apply called (D-13)."""
    from libs.tuner.recommender import TuningResult, run_tuning_cycle

    mock_call_claude.return_value = None
    mock_compute_metrics.return_value = {}
    mock_load_bounds.return_value = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
    }
    mock_build_system.return_value = "system"
    mock_build_user.return_value = "user"

    result = run_tuning_cycle(
        fills=[],
        config_dir=Path("/fake/configs"),
        bounds_path=Path("/fake/bounds.yaml"),
    )

    mock_apply.assert_not_called()
    assert isinstance(result, TuningResult)
    assert result.summary == ""
    assert result.changes == []


@patch("libs.tuner.recommender.log_parameter_change")
@patch("libs.tuner.recommender.apply_parameter_changes")
@patch("libs.tuner.recommender.load_strategy_config")
@patch("libs.tuner.recommender.compute_strategy_metrics")
@patch("libs.tuner.recommender.load_bounds_registry")
@patch("libs.tuner.recommender.build_user_message")
@patch("libs.tuner.recommender.build_system_prompt")
@patch("libs.tuner.recommender.call_claude")
def test_run_tuning_cycle_returns_summary(
    mock_call_claude: MagicMock,
    mock_build_system: MagicMock,
    mock_build_user: MagicMock,
    mock_load_bounds: MagicMock,
    mock_compute_metrics: MagicMock,
    mock_load_config: MagicMock,
    mock_apply: MagicMock,
    mock_log_change: MagicMock,
) -> None:
    """TuningResult carries Claude's summary string (D-06)."""
    from libs.tuner.recommender import TuningResult, run_tuning_cycle

    mock_call_claude.return_value = {
        "summary": "portfolio needs adjustment",
        "recommendations": [
            {
                "strategy": "momentum",
                "instrument": None,
                "param": "min_conviction",
                "value": 0.70,
                "reasoning": "low win rate",
            }
        ],
    }
    mock_compute_metrics.return_value = {}
    mock_load_config.return_value = {}
    mock_load_bounds.return_value = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
    }
    mock_build_system.return_value = "system"
    mock_build_user.return_value = "user"

    change = _make_change(param="min_conviction", reasoning="")
    mock_apply.return_value = [change]

    result = run_tuning_cycle(
        fills=[],
        config_dir=Path("/fake/configs"),
        bounds_path=Path("/fake/bounds.yaml"),
    )

    assert isinstance(result, TuningResult)
    assert result.summary == "portfolio needs adjustment"
    assert len(result.changes) == 1


@patch("libs.tuner.recommender.log_parameter_change")
@patch("libs.tuner.recommender.apply_parameter_changes")
@patch("libs.tuner.recommender.load_strategy_config")
@patch("libs.tuner.recommender.compute_strategy_metrics")
@patch("libs.tuner.recommender.load_bounds_registry")
@patch("libs.tuner.recommender.build_user_message")
@patch("libs.tuner.recommender.build_system_prompt")
@patch("libs.tuner.recommender.call_claude")
def test_run_tuning_cycle_reasoning_backfilled(
    mock_call_claude: MagicMock,
    mock_build_system: MagicMock,
    mock_build_user: MagicMock,
    mock_load_bounds: MagicMock,
    mock_compute_metrics: MagicMock,
    mock_load_config: MagicMock,
    mock_apply: MagicMock,
    mock_log_change: MagicMock,
) -> None:
    """Reasoning from Claude's recommendation is backfilled into ParameterChange."""
    from libs.tuner.recommender import TuningResult, run_tuning_cycle

    mock_call_claude.return_value = {
        "summary": "adjusting momentum",
        "recommendations": [
            {
                "strategy": "momentum",
                "instrument": None,
                "param": "min_conviction",
                "value": 0.70,
                "reasoning": "low win rate observed",
            }
        ],
    }
    mock_compute_metrics.return_value = {}
    mock_load_config.return_value = {}
    mock_load_bounds.return_value = {
        "min_conviction": _make_float_entry("min_conviction", 0.10, 0.90),
    }
    mock_build_system.return_value = "system"
    mock_build_user.return_value = "user"

    # apply returns change with empty reasoning (as writer.py produces)
    change_no_reasoning = _make_change(param="min_conviction", reasoning="")
    mock_apply.return_value = [change_no_reasoning]

    result = run_tuning_cycle(
        fills=[],
        config_dir=Path("/fake/configs"),
        bounds_path=Path("/fake/bounds.yaml"),
    )

    # The logged change should have the reasoning from Claude
    assert len(result.changes) == 1
    assert result.changes[0].reasoning == "low win rate observed"
