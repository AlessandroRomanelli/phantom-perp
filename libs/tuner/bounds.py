"""Bounds registry for tunable strategy parameters.

Provides BoundsEntry (frozen dataclass), load_bounds_registry() to parse
configs/bounds.yaml, validate_value() to assert a value is within registered
bounds, and clip_value() to clamp a value to bounds (used by Phase 13 tuner).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class BoundsEntry:
    """Immutable record of the allowable range for one tunable parameter.

    Args:
        param_name: Canonical parameter name matching strategy YAML key.
        min_value: Inclusive lower bound (cast to float).
        max_value: Inclusive upper bound (cast to float).
        value_type: Expected Python type name: "float" or "int".
    """

    param_name: str
    min_value: float
    max_value: float
    value_type: str = "float"


def load_bounds_registry(bounds_path: Path) -> dict[str, BoundsEntry]:
    """Parse a bounds YAML file and return a registry keyed by param name.

    The YAML format is::

        param_name:
          min: <number>
          max: <number>
          type: float | int

    Args:
        bounds_path: Absolute path to the bounds YAML file.

    Returns:
        Dict mapping param_name → BoundsEntry.

    Raises:
        FileNotFoundError: If bounds_path does not exist.
        ValueError: If any entry has min >= max.
    """
    if not bounds_path.exists():
        raise FileNotFoundError(f"Bounds registry not found: {bounds_path}")

    with open(bounds_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    registry: dict[str, BoundsEntry] = {}
    for param_name, spec in raw.items():
        min_val = float(spec["min"])
        max_val = float(spec["max"])
        value_type = str(spec.get("type", "float"))

        if min_val >= max_val:
            raise ValueError(
                f"Bounds entry '{param_name}': min ({min_val}) must be less than max ({max_val})"
            )

        registry[param_name] = BoundsEntry(
            param_name=param_name,
            min_value=min_val,
            max_value=max_val,
            value_type=value_type,
        )

    return registry


def validate_value(
    param_name: str,
    value: float,
    registry: dict[str, BoundsEntry],
) -> None:
    """Assert that value is within the registered bounds for param_name.

    Args:
        param_name: Name of the parameter to validate.
        value: Proposed parameter value.
        registry: Bounds registry from load_bounds_registry().

    Raises:
        ValueError: If param_name is not in the registry.
        ValueError: If value is outside [min_value, max_value].
    """
    if param_name not in registry:
        raise ValueError(
            f"Parameter '{param_name}' is not in the bounds registry. "
            f"Only registered parameters can be tuned."
        )

    entry = registry[param_name]
    if value < entry.min_value or value > entry.max_value:
        raise ValueError(
            f"Parameter '{param_name}' value {value} is outside bounds "
            f"[{entry.min_value}, {entry.max_value}]."
        )


def clip_value(
    param_name: str,
    value: float,
    registry: dict[str, BoundsEntry],
) -> float:
    """Clamp value to the registered bounds for param_name.

    Used by Phase 13 (CLAI-04) to enforce hard limits after Claude's suggestion.

    Args:
        param_name: Name of the parameter to clip.
        value: Proposed parameter value.
        registry: Bounds registry from load_bounds_registry().

    Returns:
        value clamped to [min_value, max_value].

    Raises:
        ValueError: If param_name is not in the registry.
    """
    if param_name not in registry:
        raise ValueError(
            f"Parameter '{param_name}' is not in the bounds registry. "
            f"Only registered parameters can be clipped."
        )

    entry = registry[param_name]
    return max(entry.min_value, min(entry.max_value, value))
