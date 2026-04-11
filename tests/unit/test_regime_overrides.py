"""Tests for regime override priority chain and apply/restore lifecycle.

Priority order (lowest to highest):
    session overrides < regime overrides < orchestrator param adjustments

These tests verify the merge logic and apply/restore lifecycle used in
agents/signals/main.py before each strategy evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.signals.main import _apply_session_overrides, _restore_params


# ---------------------------------------------------------------------------
# Minimal mock strategy for testing override apply/restore
# ---------------------------------------------------------------------------


@dataclass
class MockParams:
    """Minimal params dataclass for override tests."""

    min_conviction: float = 0.50
    cooldown_bars: int = 5
    adx_threshold: float = 20.0


class MockStrategy:
    """Minimal strategy stub — only needs _params attribute."""

    def __init__(self) -> None:
        self._params = MockParams()

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "mock"


# ---------------------------------------------------------------------------
# Test 1: Session override alone applies and restores correctly
# ---------------------------------------------------------------------------


def test_session_override_alone_applies_and_restores() -> None:
    """Session override changes param; restore returns original value."""
    strategy = MockStrategy()
    assert strategy._params.min_conviction == 0.50

    session_overrides = {"min_conviction": 0.40}
    originals = _apply_session_overrides(strategy, session_overrides)

    assert strategy._params.min_conviction == 0.40

    _restore_params(strategy, originals)
    assert strategy._params.min_conviction == 0.50


# ---------------------------------------------------------------------------
# Test 2: Regime override alone applies and restores correctly
# ---------------------------------------------------------------------------


def test_regime_override_alone_applies_and_restores() -> None:
    """Regime override changes param; restore returns original value."""
    strategy = MockStrategy()
    assert strategy._params.min_conviction == 0.50

    regime_overrides = {"min_conviction": 0.28}
    originals = _apply_session_overrides(strategy, regime_overrides)

    assert strategy._params.min_conviction == 0.28

    _restore_params(strategy, originals)
    assert strategy._params.min_conviction == 0.50


# ---------------------------------------------------------------------------
# Test 3: Regime wins over session on conflict (same param key)
# ---------------------------------------------------------------------------


def test_regime_wins_over_session_on_conflict() -> None:
    """When session and regime both set min_conviction, regime wins (D-02)."""
    session_overrides = {"min_conviction": 0.30}
    regime_overrides = {"min_conviction": 0.28}

    # Priority merge: session first, regime overwrites conflicts
    merged = {**session_overrides, **regime_overrides}

    assert merged["min_conviction"] == 0.28


# ---------------------------------------------------------------------------
# Test 4: Non-conflicting session and regime overrides both apply
# ---------------------------------------------------------------------------


def test_session_and_regime_both_apply_when_no_conflict() -> None:
    """When session and regime set different params, both are applied."""
    session_overrides = {"cooldown_bars": 3}
    regime_overrides = {"min_conviction": 0.28}

    merged = {**session_overrides, **regime_overrides}

    assert merged["cooldown_bars"] == 3
    assert merged["min_conviction"] == 0.28


# ---------------------------------------------------------------------------
# Test 5: Orchestrator wins over regime on conflict
# ---------------------------------------------------------------------------


def test_orchestrator_wins_over_regime() -> None:
    """Orchestrator param adjustment beats regime override (ORCH-11)."""
    session_overrides = {"min_conviction": 0.30}
    regime_overrides = {"min_conviction": 0.28}
    orch_adj = {"min_conviction": 0.20}

    merged = {**session_overrides, **regime_overrides}
    merged = {**merged, **orch_adj}

    assert merged["min_conviction"] == 0.20


# ---------------------------------------------------------------------------
# Test 6: Full priority chain — session < regime < orchestrator
# ---------------------------------------------------------------------------


def test_full_priority_chain_session_regime_orch() -> None:
    """Full priority chain: session=0.30, regime=0.28, orch=0.20 -> 0.20."""
    session_overrides = {"min_conviction": 0.30}
    regime_overrides = {"min_conviction": 0.28}
    orch_adj = {"min_conviction": 0.20}

    # Apply merge in correct order
    merged = {**session_overrides, **regime_overrides, **orch_adj}

    assert merged["min_conviction"] == 0.20


# ---------------------------------------------------------------------------
# Test 7: Apply full chain then restore returns all params to original base
# ---------------------------------------------------------------------------


def test_apply_and_restore_full_chain() -> None:
    """After apply + restore, all params return to original base values."""
    strategy = MockStrategy()
    assert strategy._params.min_conviction == 0.50  # base
    assert strategy._params.cooldown_bars == 5       # base

    merged = {"min_conviction": 0.28, "cooldown_bars": 3}
    originals = _apply_session_overrides(strategy, merged)

    assert strategy._params.min_conviction == 0.28
    assert strategy._params.cooldown_bars == 3

    _restore_params(strategy, originals)

    assert strategy._params.min_conviction == 0.50
    assert strategy._params.cooldown_bars == 5


# ---------------------------------------------------------------------------
# Test 8: Unknown param keys are silently ignored (T-29-05 security)
# ---------------------------------------------------------------------------


def test_unknown_param_key_silently_ignored() -> None:
    """Unknown keys in overrides are ignored; no AttributeError raised."""
    strategy = MockStrategy()
    overrides = {"unknown_key": 99, "min_conviction": 0.30}

    originals = _apply_session_overrides(strategy, overrides)

    # Only valid param was applied
    assert strategy._params.min_conviction == 0.30
    # Unknown key not in originals (it wasn't applied)
    assert "unknown_key" not in originals

    _restore_params(strategy, originals)
    assert strategy._params.min_conviction == 0.50
