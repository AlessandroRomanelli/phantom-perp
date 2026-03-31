"""Unit tests for libs.tuner.writer -- atomic YAML writer with post-write validation and rollback.

Tests cover:
- Atomic write via _write_atomic (same-dir temp, os.replace, cleanup on error)
- Schema detection (A = nested parameters:, B = bare keys)
- Base parameter changes (parameters block and strategy.weight)
- Per-instrument changes (Schema A and Schema B)
- Preserving unmodified keys (enabled: false, unrelated params)
- Creating new instruments block when absent (funding_arb case)
- Bounds validation before disk I/O
- Post-write validation and rollback on mismatch
- Return value (list of ParameterChange records)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from libs.tuner.bounds import load_bounds_registry
from libs.tuner.writer import (
    _apply_changes_in_memory,
    _detect_instrument_schema,
    _write_atomic,
    apply_parameter_changes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIGS_DIR = Path("configs/strategies")
BOUNDS_PATH = Path("configs/bounds.yaml")


@pytest.fixture
def registry() -> dict[str, Any]:
    """Load the real bounds registry from configs/bounds.yaml."""
    return load_bounds_registry(BOUNDS_PATH)


@pytest.fixture
def momentum_yaml(tmp_path: Path) -> Path:
    """Copy momentum.yaml to a temp dir and return the path."""
    src = CONFIGS_DIR / "momentum.yaml"
    dst = tmp_path / "momentum.yaml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def orderbook_imbalance_yaml(tmp_path: Path) -> Path:
    """Copy orderbook_imbalance.yaml to a temp dir (Schema B with enabled: false)."""
    src = CONFIGS_DIR / "orderbook_imbalance.yaml"
    dst = tmp_path / "orderbook_imbalance.yaml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def vwap_yaml(tmp_path: Path) -> Path:
    """Copy vwap.yaml to a temp dir (Schema B bare keys)."""
    src = CONFIGS_DIR / "vwap.yaml"
    dst = tmp_path / "vwap.yaml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def funding_arb_yaml(tmp_path: Path) -> Path:
    """Write a minimal funding_arb-like YAML with no instruments block (tests creation path)."""
    dst = tmp_path / "funding_arb.yaml"
    minimal = {
        "strategy": {"name": "funding_arb", "enabled": True, "weight": 0.20},
        "parameters": {
            "zscore_threshold": 2.0,
            "min_conviction": 0.55,
            "cooldown_bars": 12,
            "route_a_min_conviction": 0.70,
        },
    }
    import yaml as _yaml
    dst.write_text(_yaml.safe_dump(minimal))
    return dst


# ---------------------------------------------------------------------------
# _write_atomic tests
# ---------------------------------------------------------------------------


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    """_write_atomic writes a YAML file that can be re-read."""
    target = tmp_path / "test.yaml"
    data = {"strategy": {"name": "test"}, "parameters": {"min_conviction": 0.5}}
    _write_atomic(target, data)
    assert target.exists()
    with open(target) as f:
        loaded = yaml.safe_load(f)
    assert loaded["parameters"]["min_conviction"] == 0.5


def test_atomic_write_no_temp_left(tmp_path: Path) -> None:
    """After successful _write_atomic, no .tmp files remain in the directory."""
    target = tmp_path / "test.yaml"
    data = {"key": "value"}
    _write_atomic(target, data)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_atomic_write_cleanup_on_error(tmp_path: Path) -> None:
    """If yaml.safe_dump raises, temp file is cleaned up and target is unchanged."""
    target = tmp_path / "test.yaml"
    original_content = "strategy:\n  name: original\n"
    target.write_text(original_content)

    with patch("libs.tuner.writer.yaml.safe_dump", side_effect=RuntimeError("dump error")):
        with pytest.raises(RuntimeError, match="dump error"):
            _write_atomic(target, {"key": "value"})

    # Original file unchanged
    assert target.read_text() == original_content
    # No temp files left
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# _detect_instrument_schema tests
# ---------------------------------------------------------------------------


def test_schema_detection_a() -> None:
    """_detect_instrument_schema returns 'A' if instrument block has nested parameters: dict."""
    block: dict[str, Any] = {"parameters": {"min_conviction": 0.35, "cooldown_bars": 4}}
    assert _detect_instrument_schema(block) == "A"


def test_schema_detection_b() -> None:
    """_detect_instrument_schema returns 'B' for bare key instrument block."""
    block: dict[str, Any] = {"imbalance_threshold": 0.30, "max_spread_bps": 10.0}
    assert _detect_instrument_schema(block) == "B"


def test_schema_detection_b_with_enabled() -> None:
    """_detect_instrument_schema returns 'B' for enabled: false only block (no parameters key)."""
    block: dict[str, Any] = {"enabled": False}
    assert _detect_instrument_schema(block) == "B"


def test_schema_detection_a_non_dict_parameters() -> None:
    """_detect_instrument_schema returns 'B' if parameters key is not a dict."""
    block: dict[str, Any] = {"parameters": "not_a_dict"}
    assert _detect_instrument_schema(block) == "B"


# ---------------------------------------------------------------------------
# _apply_changes_in_memory tests
# ---------------------------------------------------------------------------


def test_deep_copy_not_mutate_original() -> None:
    """_apply_changes_in_memory returns a deep copy; original dict is not mutated."""
    data: dict[str, Any] = {
        "strategy": {"name": "momentum", "weight": 0.20},
        "parameters": {"min_conviction": 0.40, "cooldown_bars": 5},
    }
    original_conviction = data["parameters"]["min_conviction"]
    _apply_changes_in_memory(data, {"min_conviction": 0.30}, {})
    assert data["parameters"]["min_conviction"] == original_conviction


# ---------------------------------------------------------------------------
# apply_parameter_changes -- base parameter changes
# ---------------------------------------------------------------------------


def test_apply_base_param_change(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Applying changes to base parameters writes the new value and leaves others unchanged."""
    changes = apply_parameter_changes(
        momentum_yaml,
        changes={"min_conviction": 0.30},
        instrument_changes={},
        registry=registry,
    )
    with open(momentum_yaml) as f:
        reloaded = yaml.safe_load(f)

    assert reloaded["parameters"]["min_conviction"] == pytest.approx(0.30, abs=1e-9)
    # Other params must remain
    assert reloaded["parameters"]["fast_ema_period"] == 12
    assert reloaded["parameters"]["adx_threshold"] == pytest.approx(20.0, abs=1e-9)
    # Return value should include one ParameterChange
    assert len(changes) == 1
    assert changes[0].param == "min_conviction"
    assert changes[0].new_value == pytest.approx(0.30, abs=1e-9)


def test_preserves_unmodified_params(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Changing one base param preserves fast_ema_period, slow_ema_period, adx_threshold."""
    apply_parameter_changes(
        momentum_yaml,
        changes={"min_conviction": 0.30},
        instrument_changes={},
        registry=registry,
    )
    with open(momentum_yaml) as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["parameters"]["fast_ema_period"] == 12
    assert reloaded["parameters"]["slow_ema_period"] == 26
    assert reloaded["parameters"]["adx_threshold"] == pytest.approx(20.0, abs=1e-9)


def test_weight_change_at_strategy_level(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Weight changes update strategy.weight, not parameters.weight."""
    apply_parameter_changes(
        momentum_yaml,
        changes={"weight": 0.25},
        instrument_changes={},
        registry=registry,
    )
    with open(momentum_yaml) as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["strategy"]["weight"] == pytest.approx(0.25, abs=1e-9)
    # Should NOT appear in parameters
    assert "weight" not in reloaded.get("parameters", {})


# ---------------------------------------------------------------------------
# apply_parameter_changes -- instrument-level Schema A (nested parameters:)
# ---------------------------------------------------------------------------


def test_apply_instrument_param_schema_a(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Schema A: instrument param written to instruments.ETH-PERP.parameters.min_conviction."""
    apply_parameter_changes(
        momentum_yaml,
        changes={},
        instrument_changes={"ETH-PERP": {"min_conviction": 0.25}},
        registry=registry,
    )
    with open(momentum_yaml) as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["instruments"]["ETH-PERP"]["parameters"]["min_conviction"] == pytest.approx(
        0.25, abs=1e-9
    )
    # Other ETH-PERP instrument params remain unchanged
    assert reloaded["instruments"]["ETH-PERP"]["parameters"]["cooldown_bars"] == 8
    # Other instruments untouched
    assert reloaded["instruments"]["BTC-PERP"]["parameters"]["cooldown_bars"] == 16


# ---------------------------------------------------------------------------
# apply_parameter_changes -- instrument-level Schema B (bare keys)
# ---------------------------------------------------------------------------


def test_apply_instrument_param_schema_b(
    orderbook_imbalance_yaml: Path, registry: dict[str, Any]
) -> None:
    """Schema B: instrument param written as bare key instruments.BTC-PERP.imbalance_threshold."""
    # imbalance_threshold is not in bounds.yaml -- use min_conviction which is
    changes = apply_parameter_changes(
        orderbook_imbalance_yaml,
        changes={},
        instrument_changes={"BTC-PERP": {"min_conviction": 0.35}},
        registry=registry,
    )
    with open(orderbook_imbalance_yaml) as f:
        reloaded = yaml.safe_load(f)
    # Schema B: bare key at instrument level, NOT nested under parameters:
    assert reloaded["instruments"]["BTC-PERP"]["min_conviction"] == pytest.approx(0.35, abs=1e-9)
    assert len(changes) == 1


def test_preserves_enabled_false(
    orderbook_imbalance_yaml: Path, registry: dict[str, Any]
) -> None:
    """Applying instrument changes preserves enabled: false on other instruments (ETH-PERP, SOL-PERP)."""
    apply_parameter_changes(
        orderbook_imbalance_yaml,
        changes={},
        instrument_changes={"BTC-PERP": {"min_conviction": 0.35}},
        registry=registry,
    )
    with open(orderbook_imbalance_yaml) as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["instruments"]["ETH-PERP"]["enabled"] is False
    assert reloaded["instruments"]["SOL-PERP"]["enabled"] is False


# ---------------------------------------------------------------------------
# apply_parameter_changes -- creates instruments block when absent (funding_arb)
# ---------------------------------------------------------------------------


def test_creates_instruments_block(funding_arb_yaml: Path, registry: dict[str, Any]) -> None:
    """When no instruments block exists, one is created with Schema A format."""
    apply_parameter_changes(
        funding_arb_yaml,
        changes={},
        instrument_changes={"ETH-PERP": {"min_conviction": 0.50}},
        registry=registry,
    )
    with open(funding_arb_yaml) as f:
        reloaded = yaml.safe_load(f)
    # Should have created instruments block with Schema A nested parameters
    assert "instruments" in reloaded
    assert "ETH-PERP" in reloaded["instruments"]
    assert reloaded["instruments"]["ETH-PERP"]["parameters"]["min_conviction"] == pytest.approx(
        0.50, abs=1e-9
    )


# ---------------------------------------------------------------------------
# apply_parameter_changes -- bounds validation
# ---------------------------------------------------------------------------


def test_validates_against_bounds(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Applying a value outside bounds raises ValueError containing 'outside bounds'."""
    with pytest.raises(ValueError, match="outside bounds"):
        apply_parameter_changes(
            momentum_yaml,
            changes={"min_conviction": 5.0},
            instrument_changes={},
            registry=registry,
        )


def test_validates_unregistered_param(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Applying a param not in bounds registry raises ValueError with 'not in the bounds registry'."""
    with pytest.raises(ValueError, match="not in the bounds registry"):
        apply_parameter_changes(
            momentum_yaml,
            changes={"fast_ema_period": 20},
            instrument_changes={},
            registry=registry,
        )


def test_validates_instrument_param_against_bounds(
    momentum_yaml: Path, registry: dict[str, Any]
) -> None:
    """Instrument-level param also validated: min_conviction=1.5 raises outside bounds."""
    with pytest.raises(ValueError, match="outside bounds"):
        apply_parameter_changes(
            momentum_yaml,
            changes={},
            instrument_changes={"ETH-PERP": {"min_conviction": 1.5}},
            registry=registry,
        )


def test_validates_before_disk_write(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """If validation fails, target file is NOT written (content stays identical)."""
    original_mtime = momentum_yaml.stat().st_mtime
    with pytest.raises(ValueError):
        apply_parameter_changes(
            momentum_yaml,
            changes={"min_conviction": 99.0},
            instrument_changes={},
            registry=registry,
        )
    # File modification time should be unchanged (no disk I/O happened)
    assert momentum_yaml.stat().st_mtime == original_mtime


# ---------------------------------------------------------------------------
# apply_parameter_changes -- post-write validation and rollback
# ---------------------------------------------------------------------------


def test_post_write_validation_pass(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """Normal write completes without error; re-reading file matches intent."""
    apply_parameter_changes(
        momentum_yaml,
        changes={"min_conviction": 0.35},
        instrument_changes={},
        registry=registry,
    )
    with open(momentum_yaml) as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["parameters"]["min_conviction"] == pytest.approx(0.35, abs=1e-9)


def test_post_write_validation_rollback(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """If post-write re-parse mismatches intent, raises ValueError and original file is restored."""
    original_content = momentum_yaml.read_text()

    # Patch yaml.safe_load to return corrupted data on the second call (post-write read)
    real_safe_load = yaml.safe_load
    call_count = 0

    def fake_safe_load(stream: Any) -> Any:
        nonlocal call_count
        result = real_safe_load(stream)
        call_count += 1
        # First call is the initial read, second call is post-write validation
        if call_count == 2:
            # Corrupt the returned data
            result["parameters"]["min_conviction"] = 0.99  # mismatch from intended 0.35
        return result

    with patch("libs.tuner.writer.yaml.safe_load", side_effect=fake_safe_load):
        with pytest.raises(ValueError, match="Post-write validation failed"):
            apply_parameter_changes(
                momentum_yaml,
                changes={"min_conviction": 0.35},
                instrument_changes={},
                registry=registry,
            )

    # Original content must be restored
    assert momentum_yaml.read_text() == original_content


# ---------------------------------------------------------------------------
# apply_parameter_changes -- return value
# ---------------------------------------------------------------------------


def test_returns_change_records(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """apply_parameter_changes returns list of ParameterChange with correct old/new values."""
    changes = apply_parameter_changes(
        momentum_yaml,
        changes={"min_conviction": 0.30},
        instrument_changes={"ETH-PERP": {"adx_threshold": 15.0}},
        registry=registry,
    )
    assert len(changes) == 2
    # Find base-level change
    base_change = next(c for c in changes if c.instrument is None)
    assert base_change.param == "min_conviction"
    assert base_change.old_value == pytest.approx(0.40, abs=1e-9)
    assert base_change.new_value == pytest.approx(0.30, abs=1e-9)
    # Find instrument-level change
    inst_change = next(c for c in changes if c.instrument == "ETH-PERP")
    assert inst_change.param == "adx_threshold"
    assert inst_change.old_value == pytest.approx(18.0, abs=1e-9)
    assert inst_change.new_value == pytest.approx(15.0, abs=1e-9)


def test_no_changes_returns_empty_list(momentum_yaml: Path, registry: dict[str, Any]) -> None:
    """apply_parameter_changes with empty changes/instrument_changes returns empty list."""
    changes = apply_parameter_changes(
        momentum_yaml,
        changes={},
        instrument_changes={},
        registry=registry,
    )
    assert changes == []
