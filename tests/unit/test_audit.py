"""Unit tests for libs/tuner/audit.py.

Coverage:
- log_parameter_change emits "tuner_parameter_changed" with all fields
- log_parameter_change with instrument=None (base-level param)
- log_no_change emits "tuner_no_change" with strategy, instrument, reasoning, timestamp
- log_no_change with instrument=None (base-level)
- ParameterChange is a frozen dataclass (FrozenInstanceError)
- ParameterChange fields accessible: strategy, instrument, param, old_value, new_value, reasoning, timestamp
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
import structlog.testing

from libs.tuner.audit import ParameterChange, log_no_change, log_parameter_change


def _make_change(
    *,
    strategy: str = "momentum",
    instrument: str | None = "ETH-PERP",
    param: str = "min_conviction",
    old_value: float = 0.35,
    new_value: float = 0.30,
    reasoning: str = "test reason",
    timestamp: datetime | None = None,
) -> ParameterChange:
    """Build a ParameterChange for testing."""
    if timestamp is None:
        timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    return ParameterChange(
        strategy=strategy,
        instrument=instrument,
        param=param,
        old_value=old_value,
        new_value=new_value,
        reasoning=reasoning,
        timestamp=timestamp,
    )


def test_log_parameter_change_emits_event() -> None:
    """log_parameter_change should emit a 'tuner_parameter_changed' event with all fields."""
    change = _make_change()
    with structlog.testing.capture_logs() as logs:
        log_parameter_change(change)

    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "tuner_parameter_changed"
    assert entry["strategy"] == "momentum"
    assert entry["instrument"] == "ETH-PERP"
    assert entry["param"] == "min_conviction"
    assert entry["old_value"] == 0.35
    assert entry["new_value"] == 0.30
    assert entry["reasoning"] == "test reason"
    assert "timestamp" in entry


def test_log_parameter_change_base_level() -> None:
    """log_parameter_change with instrument=None should log instrument as None."""
    change = _make_change(instrument=None)
    with structlog.testing.capture_logs() as logs:
        log_parameter_change(change)

    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "tuner_parameter_changed"
    assert entry["instrument"] is None


def test_log_no_change_emits_event() -> None:
    """log_no_change should emit a 'tuner_no_change' event with required fields."""
    with structlog.testing.capture_logs() as logs:
        log_no_change(
            strategy="momentum",
            instrument="ETH-PERP",
            reasoning="performance adequate",
        )

    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "tuner_no_change"
    assert entry["strategy"] == "momentum"
    assert entry["instrument"] == "ETH-PERP"
    assert entry["reasoning"] == "performance adequate"
    assert "timestamp" in entry


def test_log_no_change_base_level() -> None:
    """log_no_change with instrument=None should log instrument as None."""
    with structlog.testing.capture_logs() as logs:
        log_no_change(
            strategy="momentum",
            instrument=None,
            reasoning="no change needed",
        )

    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "tuner_no_change"
    assert entry["instrument"] is None


def test_parameter_change_is_frozen() -> None:
    """Attempting to set a field on ParameterChange should raise FrozenInstanceError."""
    change = _make_change()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        change.strategy = "x"  # type: ignore[misc]


def test_parameter_change_fields() -> None:
    """All 7 fields of ParameterChange should be accessible."""
    ts = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    change = ParameterChange(
        strategy="vwap",
        instrument="BTC-PERP",
        param="cooldown_bars",
        old_value=5,
        new_value=8,
        reasoning="high churn detected",
        timestamp=ts,
    )
    assert change.strategy == "vwap"
    assert change.instrument == "BTC-PERP"
    assert change.param == "cooldown_bars"
    assert change.old_value == 5
    assert change.new_value == 8
    assert change.reasoning == "high churn detected"
    assert change.timestamp == ts
