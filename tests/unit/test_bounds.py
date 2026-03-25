"""Unit tests for libs/tuner/bounds.py.

Coverage:
- Loading bounds.yaml returns 8 BoundsEntry values
- BoundsEntry fields (min_value, max_value, value_type)
- Integer-typed bounds entries
- validate_value: in-range, boundary, below min, above max, unregistered param
- Validation of bounds YAML where min >= max
- BoundsEntry is a frozen dataclass (FrozenInstanceError)
- load_bounds_registry raises FileNotFoundError for missing file
- clip_value: clips below min, clips above max, passes through in-range, unregistered param raises
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from libs.tuner.bounds import BoundsEntry, clip_value, load_bounds_registry, validate_value


BOUNDS_YAML = Path(__file__).resolve().parent.parent.parent / "configs" / "bounds.yaml"


@pytest.fixture()
def registry() -> dict[str, BoundsEntry]:
    """Load the real bounds registry from configs/bounds.yaml."""
    return load_bounds_registry(BOUNDS_YAML)


def test_load_bounds_registry_returns_all_entries(registry: dict[str, BoundsEntry]) -> None:
    """Loading configs/bounds.yaml should return exactly 8 entries."""
    assert len(registry) == 8


def test_load_bounds_registry_contains_min_conviction(registry: dict[str, BoundsEntry]) -> None:
    """Registry must contain min_conviction key."""
    assert "min_conviction" in registry


def test_load_bounds_registry_contains_weight(registry: dict[str, BoundsEntry]) -> None:
    """Registry must contain weight key."""
    assert "weight" in registry


def test_bounds_entry_fields(registry: dict[str, BoundsEntry]) -> None:
    """min_conviction entry should have correct min, max, and type."""
    entry = registry["min_conviction"]
    assert entry.min_value == 0.10
    assert entry.max_value == 0.90
    assert entry.value_type == "float"


def test_bounds_entry_int_type(registry: dict[str, BoundsEntry]) -> None:
    """cooldown_bars entry should have int value_type and correct bounds."""
    entry = registry["cooldown_bars"]
    assert entry.value_type == "int"
    assert entry.min_value == 1.0
    assert entry.max_value == 30.0


def test_validate_value_in_range(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should not raise for a value within bounds."""
    validate_value("min_conviction", 0.5, registry)  # must not raise


def test_validate_value_at_min_boundary(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should not raise for a value at the min boundary."""
    validate_value("min_conviction", 0.10, registry)  # must not raise


def test_validate_value_at_max_boundary(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should not raise for a value at the max boundary."""
    validate_value("min_conviction", 0.90, registry)  # must not raise


def test_validate_value_below_min(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should raise ValueError containing 'outside bounds' and '[0.1, 0.9]'."""
    with pytest.raises(ValueError, match="outside bounds"):
        validate_value("min_conviction", 0.05, registry)


def test_validate_value_below_min_includes_range(registry: dict[str, BoundsEntry]) -> None:
    """ValueError message should include the bounds range."""
    with pytest.raises(ValueError, match=r"\[0\.1.*0\.9\]"):
        validate_value("min_conviction", 0.05, registry)


def test_validate_value_above_max(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should raise ValueError for a value above max."""
    with pytest.raises(ValueError, match="outside bounds"):
        validate_value("min_conviction", 0.95, registry)


def test_validate_unregistered_param(registry: dict[str, BoundsEntry]) -> None:
    """validate_value should raise ValueError for a param not in the registry."""
    with pytest.raises(ValueError, match="not in the bounds registry"):
        validate_value("fast_ema_period", 12.0, registry)


def test_bounds_min_equals_max_raises(tmp_path: Path) -> None:
    """loading a bounds YAML where min >= max raises ValueError containing 'min' and 'max'."""
    bad_yaml = tmp_path / "bounds.yaml"
    bad_yaml.write_text("bad_param:\n  min: 0.5\n  max: 0.5\n  type: float\n")
    with pytest.raises(ValueError, match="min"):
        load_bounds_registry(bad_yaml)


def test_bounds_entry_is_frozen(registry: dict[str, BoundsEntry]) -> None:
    """Attempting to set entry.min_value should raise FrozenInstanceError."""
    entry = registry["min_conviction"]
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        entry.min_value = 99  # type: ignore[misc]


def test_load_missing_file_raises() -> None:
    """load_bounds_registry should raise FileNotFoundError for a missing file."""
    with pytest.raises(FileNotFoundError):
        load_bounds_registry(Path("/nonexistent.yaml"))


def test_clip_value_below_min(registry: dict[str, BoundsEntry]) -> None:
    """clip_value should return min_value when input is below min."""
    result = clip_value("min_conviction", 0.05, registry)
    assert result == 0.10


def test_clip_value_above_max(registry: dict[str, BoundsEntry]) -> None:
    """clip_value should return max_value when input is above max."""
    result = clip_value("min_conviction", 0.95, registry)
    assert result == 0.90


def test_clip_value_in_range(registry: dict[str, BoundsEntry]) -> None:
    """clip_value should return the value unchanged when it's within bounds."""
    result = clip_value("min_conviction", 0.5, registry)
    assert result == 0.5


def test_clip_unregistered_param(registry: dict[str, BoundsEntry]) -> None:
    """clip_value should raise ValueError for an unregistered param."""
    with pytest.raises(ValueError):
        clip_value("fast_ema_period", 12.0, registry)
