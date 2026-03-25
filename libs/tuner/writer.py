"""Atomic YAML writer for strategy config files.

Provides apply_parameter_changes() as the single entry point for the tuner
to safely modify strategy YAML configs. Every write is atomic (os.replace
with same-directory temp file), every write is validated (re-parse + compare),
and any mismatch triggers rollback to the original file content.
"""

from __future__ import annotations

import contextlib
import copy
import os
import tempfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from libs.tuner.audit import ParameterChange
from libs.tuner.bounds import BoundsEntry, validate_value


def _detect_instrument_schema(instrument_block: dict[str, Any]) -> str:
    """Detect whether an instrument override block uses Schema A or Schema B.

    Schema A: has a "parameters" key that is a dict (nested overrides).
    Schema B: bare keys at instrument level (e.g. imbalance_threshold, enabled).

    Args:
        instrument_block: The dict under instruments.<INSTRUMENT_ID>.

    Returns:
        "A" if instrument_block has a "parameters" key pointing to a dict,
        "B" otherwise.
    """
    params = instrument_block.get("parameters")
    if isinstance(params, dict):
        return "A"
    return "B"


def _write_atomic(target_path: Path, data: dict[str, Any]) -> None:
    """Write data to target_path atomically using a same-directory temp file.

    Creates a temp file in the same directory as target_path (guaranteeing
    same-filesystem), writes YAML, then calls os.replace for an atomic swap.
    On any error the temp file is removed and the exception is re-raised.

    Args:
        target_path: Destination file path.
        data: Data to serialize as YAML.

    Raises:
        Any exception from yaml.safe_dump or os.replace.
    """
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path_str, target_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path_str)
        raise


def _write_bytes_atomic(target_path: Path, data: bytes) -> None:
    """Write raw bytes to target_path atomically using a same-directory temp file.

    Used for rollback to restore the exact original file content.

    Args:
        target_path: Destination file path.
        data: Raw bytes to write.
    """
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path_str, target_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path_str)
        raise


def _apply_changes_in_memory(
    data: dict[str, Any],
    changes: dict[str, Any],
    instrument_changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Apply changes to a deep copy of data and return the modified copy.

    Does not modify the original data dict.

    For base changes:
    - "weight" is written to result["strategy"]["weight"]
    - All other keys are written to result["parameters"][key]

    For instrument_changes:
    - If the instruments block is absent, it is created
    - If the instrument block is absent, a new Schema A block is created
    - If the instrument block exists, its schema (A or B) is respected

    Args:
        data: Original YAML content as a dict.
        changes: Base-level parameter changes (key -> new value).
        instrument_changes: Per-instrument changes ({instrument: {param: value}}).

    Returns:
        Deep copy of data with all changes applied.
    """
    result: dict[str, Any] = copy.deepcopy(data)

    # Apply base-level changes
    for param, value in changes.items():
        if param == "weight":
            result.setdefault("strategy", {})["weight"] = value
        else:
            result.setdefault("parameters", {})[param] = value

    # Apply per-instrument changes
    if instrument_changes:
        result.setdefault("instruments", {})

        for instrument, inst_params in instrument_changes.items():
            instruments_block = result["instruments"]

            if instrument not in instruments_block:
                # Create new Schema A block for instruments that don't exist yet
                instruments_block[instrument] = {"parameters": {}}

            inst_block = instruments_block[instrument]
            schema = _detect_instrument_schema(inst_block)

            for param, value in inst_params.items():
                if schema == "A":
                    inst_block.setdefault("parameters", {})[param] = value
                else:
                    # Schema B: write bare key at instrument level
                    inst_block[param] = value

    return result


def _compare_values(a: Any, b: Any, value_type: str) -> bool:
    """Compare two values for post-write validation equality.

    Uses float tolerance for floats and exact equality for ints.

    Args:
        a: First value.
        b: Second value.
        value_type: "float" or "int" from bounds registry.

    Returns:
        True if values are considered equal.
    """
    if value_type == "int":
        return int(a) == int(b)
    # float comparison with tolerance
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def _collect_all_changes(
    strategy_name: str,
    current_data: dict[str, Any],
    changes: dict[str, Any],
    instrument_changes: dict[str, dict[str, Any]],
    registry: dict[str, BoundsEntry],
) -> list[ParameterChange]:
    """Build a list of ParameterChange records by diffing current vs intended values.

    Args:
        strategy_name: Name of the strategy (from current_data["strategy"]["name"]).
        current_data: YAML content before modification.
        changes: Base-level intended changes.
        instrument_changes: Per-instrument intended changes.
        registry: Bounds registry used to determine value_type for comparison.

    Returns:
        List of ParameterChange records for each changed parameter.
    """
    records: list[ParameterChange] = []
    now = datetime.now(UTC)

    for param, new_value in changes.items():
        if param == "weight":
            old_value = current_data.get("strategy", {}).get("weight")
        else:
            old_value = current_data.get("parameters", {}).get(param)

        records.append(
            ParameterChange(
                strategy=strategy_name,
                instrument=None,
                param=param,
                old_value=old_value,
                new_value=new_value,
                reasoning="",
                timestamp=now,
            )
        )

    for instrument, inst_params in instrument_changes.items():
        inst_block = current_data.get("instruments", {}).get(instrument, {})

        for param, new_value in inst_params.items():
            if _detect_instrument_schema(inst_block) == "A":
                old_value = inst_block.get("parameters", {}).get(param)
            else:
                old_value = inst_block.get(param)

            records.append(
                ParameterChange(
                    strategy=strategy_name,
                    instrument=instrument,
                    param=param,
                    old_value=old_value,
                    new_value=new_value,
                    reasoning="",
                    timestamp=now,
                )
            )

    return records


def _validate_all_changes(
    changes: dict[str, Any],
    instrument_changes: dict[str, dict[str, Any]],
    registry: dict[str, BoundsEntry],
) -> None:
    """Validate all proposed changes against the bounds registry.

    Validates every (param, value) pair in both changes and instrument_changes.
    Raises ValueError on first invalid entry.

    Args:
        changes: Base-level parameter changes.
        instrument_changes: Per-instrument parameter changes.
        registry: Bounds registry from load_bounds_registry().

    Raises:
        ValueError: If any param is not in the registry or value is out of bounds.
    """
    for param, value in changes.items():
        validate_value(param, float(value), registry)

    for _instrument, inst_params in instrument_changes.items():
        for param, value in inst_params.items():
            validate_value(param, float(value), registry)


def _validate_post_write(
    written_data: dict[str, Any],
    intended_data: dict[str, Any],
    registry: dict[str, BoundsEntry],
) -> list[str]:
    """Compare written YAML against intended data and return list of mismatch descriptions.

    Compares all scalar values recursively. Returns an empty list if everything matches.

    Args:
        written_data: YAML re-parsed from disk after write.
        intended_data: The data dict that was intended to be written.
        registry: Used to determine value_type for float vs int comparison.

    Returns:
        List of human-readable mismatch descriptions (empty if all match).
    """
    mismatches: list[str] = []
    _compare_dicts(written_data, intended_data, registry, [], mismatches)
    return mismatches


def _compare_dicts(
    written: Any,
    intended: Any,
    registry: dict[str, BoundsEntry],
    path: list[str],
    mismatches: list[str],
) -> None:
    """Recursively compare written vs intended, recording mismatches."""
    if isinstance(intended, dict) and isinstance(written, dict):
        for key, intended_val in intended.items():
            written_val = written.get(key)
            _compare_dicts(written_val, intended_val, registry, [*path, str(key)], mismatches)
    elif isinstance(intended, (int, float)) and isinstance(written, (int, float)):
        # Determine value_type from registry if param name is in path
        param_name = path[-1] if path else ""
        entry = registry.get(param_name)
        value_type = entry.value_type if entry else "float"
        if not _compare_values(written, intended, value_type):
            mismatches.append(
                f"{'.'.join(path)}: written={written!r}, intended={intended!r}"
            )
    else:
        if written != intended:
            mismatches.append(
                f"{'.'.join(path)}: written={written!r}, intended={intended!r}"
            )


def apply_parameter_changes(
    strategy_path: Path,
    changes: dict[str, Any],
    instrument_changes: dict[str, dict[str, Any]],
    registry: dict[str, BoundsEntry],
) -> list[ParameterChange]:
    """Atomically apply parameter changes to a strategy YAML config file.

    All proposed values are validated against the bounds registry before any
    disk I/O. The write is atomic (os.replace with same-directory temp file).
    After writing, the file is re-parsed and compared against the intended
    content. Any mismatch triggers a rollback to the original content and
    raises ValueError.

    Args:
        strategy_path: Path to the strategy YAML config file.
        changes: Base-level parameter changes {param: new_value}.
            Use "weight" to update strategy.weight.
        instrument_changes: Per-instrument changes {instrument: {param: new_value}}.
        registry: Bounds registry from load_bounds_registry().

    Returns:
        List of ParameterChange records for each changed parameter.
        Empty list if changes and instrument_changes are both empty.

    Raises:
        ValueError: If any param is not in the registry or value is out of bounds.
        ValueError: If post-write validation detects a mismatch (rollback applied).
    """
    # Early return: nothing to do
    if not changes and not instrument_changes:
        return []

    # Step 1: Validate all values against bounds BEFORE touching disk
    _validate_all_changes(changes, instrument_changes, registry)

    # Step 2: Load current YAML and capture original bytes for exact rollback.
    original_bytes = strategy_path.read_bytes()
    with open(strategy_path) as f:
        current_data: dict[str, Any] = yaml.safe_load(f)

    # Step 3: Extract strategy name for ParameterChange records
    strategy_name: str = current_data.get("strategy", {}).get("name", strategy_path.stem)

    # Step 4: Build ParameterChange records before modification
    change_records = _collect_all_changes(
        strategy_name, current_data, changes, instrument_changes, registry
    )

    # Step 5: Apply changes to a deep copy
    modified_data = _apply_changes_in_memory(current_data, changes, instrument_changes)

    # Step 6: Atomic write
    _write_atomic(strategy_path, modified_data)

    # Step 8: Post-write validation
    with open(strategy_path) as f:
        written_data: dict[str, Any] = yaml.safe_load(f)

    mismatches = _validate_post_write(written_data, modified_data, registry)
    if mismatches:
        # Rollback to original bytes (exact byte-for-byte restore, not yaml round-trip)
        _write_bytes_atomic(strategy_path, original_bytes)
        raise ValueError(f"Post-write validation failed: {mismatches}")

    # Step 9: Return change records
    return change_records
