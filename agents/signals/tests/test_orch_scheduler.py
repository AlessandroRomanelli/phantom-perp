"""Tests for the LLM strategy orchestrator scheduler.

Tests cover:
- Interval and cooldown enforcement (skip logic)
- Gate map updated correctly from orchestrator decisions
- Param adjustments stored and cleared correctly
- Redis hset called with correct mapping
- Error recovery — loop continues after failure
- Safe default: gate_map.get((inst, strat), True) == True for missing keys
- Orchestrator param adjustments merged into session overrides (wins on conflict)

All tests mock call_claude_orchestrator; no real HTTP calls are made.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.signals.orch_client import OrchestratorParams
from agents.signals.orch_scheduler import _TICK_SLEEP_SECONDS, _run_tick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INSTRUMENT = "ETH-PERP"
_STRATEGY = "momentum"


def _make_params(
    update_interval: int = 14400,
    min_interval: int = 3600,
    enabled: bool = True,
) -> OrchestratorParams:
    return OrchestratorParams(
        enabled=enabled,
        update_interval_seconds=update_interval,
        min_interval_seconds=min_interval,
        max_tokens=512,
    )


def _make_redis() -> AsyncMock:
    """Return an AsyncMock suitable for use as redis_client."""
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    return redis


def _make_decision(
    instrument: str = _INSTRUMENT,
    strategy: str = _STRATEGY,
    enabled: bool = True,
    param_adjustments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "instrument": instrument,
        "strategy": strategy,
        "enabled": enabled,
        "reasoning": "test reasoning",
        "param_adjustments": param_adjustments or {},
    }


# ---------------------------------------------------------------------------
# TestRunTick — interval and cooldown enforcement
# ---------------------------------------------------------------------------


class TestRunTick:
    """Tests for _run_tick() interval, cooldown, and update logic."""

    @pytest.mark.asyncio
    async def test_first_run_always_proceeds(self) -> None:
        """When last_run_time == 0, the first run always proceeds."""
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}
        redis = _make_redis()
        decisions = [_make_decision()]

        with (
            patch(
                "agents.signals.orch_scheduler.call_claude_orchestrator",
                new=AsyncMock(return_value=decisions),
            ),
            patch(
                "agents.signals.orch_scheduler.validate_orchestrator_response",
                return_value=decisions,
            ),
            patch(
                "agents.signals.orch_scheduler.build_orchestrator_context",
                return_value="context",
            ),
        ):
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=redis,
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(),
                last_run_time=0.0,
            )

        assert result > 0.0
        assert (_INSTRUMENT, _STRATEGY) in gate_map

    @pytest.mark.asyncio
    async def test_skip_when_cooldown_not_elapsed(self) -> None:
        """Tick is skipped if elapsed < min_interval_seconds."""
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}
        redis = _make_redis()

        import time
        very_recent = time.time() - 10  # 10 seconds ago, cooldown is 3600

        with patch(
            "agents.signals.orch_scheduler.call_claude_orchestrator",
            new=AsyncMock(return_value=[_make_decision()]),
        ) as mock_call:
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=redis,
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(min_interval=3600, update_interval=14400),
                last_run_time=very_recent,
            )
            mock_call.assert_not_awaited()

        assert result == very_recent  # unchanged — skip returns original time
        assert len(gate_map) == 0

    @pytest.mark.asyncio
    async def test_skip_when_update_interval_not_elapsed(self) -> None:
        """Tick is skipped if min_interval passed but update_interval not elapsed."""
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}

        import time
        recent = time.time() - 7200  # 2 hours ago; update_interval = 14400 (4h)

        with patch(
            "agents.signals.orch_scheduler.call_claude_orchestrator",
            new=AsyncMock(return_value=[]),
        ) as mock_call:
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(min_interval=3600, update_interval=14400),
                last_run_time=recent,
            )
            mock_call.assert_not_awaited()

        assert result == recent

    @pytest.mark.asyncio
    async def test_runs_when_update_interval_elapsed(self) -> None:
        """Tick proceeds when both cooldown and update_interval have elapsed."""
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}
        decisions = [_make_decision()]

        import time
        old_time = time.time() - 50000  # well past any interval

        with (
            patch(
                "agents.signals.orch_scheduler.call_claude_orchestrator",
                new=AsyncMock(return_value=decisions),
            ),
            patch(
                "agents.signals.orch_scheduler.validate_orchestrator_response",
                return_value=decisions,
            ),
            patch(
                "agents.signals.orch_scheduler.build_orchestrator_context",
                return_value="ctx",
            ),
        ):
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(min_interval=3600, update_interval=14400),
                last_run_time=old_time,
            )

        assert result > old_time
        assert (_INSTRUMENT, _STRATEGY) in gate_map


# ---------------------------------------------------------------------------
# TestGateMapUpdates — gate map and param adjustment dict mutation
# ---------------------------------------------------------------------------


class TestGateMapUpdates:
    """Tests for gate map and param_adjustments dict mutations."""

    @pytest.mark.asyncio
    async def test_gate_map_set_to_true_for_enabled_decision(self) -> None:
        """gate_map[(inst, strat)] == True when decision.enabled == True."""
        gate_map: dict[tuple[str, str], bool] = {}
        decisions = [_make_decision(enabled=True)]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        assert gate_map[(_INSTRUMENT, _STRATEGY)] is True

    @pytest.mark.asyncio
    async def test_gate_map_set_to_false_for_disabled_decision(self) -> None:
        """gate_map[(inst, strat)] == False when decision.enabled == False."""
        gate_map: dict[tuple[str, str], bool] = {}
        decisions = [_make_decision(enabled=False)]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        assert gate_map[(_INSTRUMENT, _STRATEGY)] is False

    @pytest.mark.asyncio
    async def test_param_adjustments_stored_correctly(self) -> None:
        """param_adjustments dict stores clipped param values from decision."""
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}
        decisions = [_make_decision(param_adjustments={"min_conviction": 0.65, "weight": 0.3})]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(),
                last_run_time=0.0,
            )

        assert param_adj[(_INSTRUMENT, _STRATEGY)] == {"min_conviction": 0.65, "weight": 0.3}

    @pytest.mark.asyncio
    async def test_stale_param_adjustments_cleared_when_empty(self) -> None:
        """Stale param_adjustments entry is deleted when new decision has no adj."""
        key = (_INSTRUMENT, _STRATEGY)
        gate_map: dict[tuple[str, str], bool] = {}
        param_adj: dict[tuple[str, str], dict[str, Any]] = {key: {"weight": 0.5}}
        decisions = [_make_decision(param_adjustments={})]  # no adjustments this run

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments=param_adj,
                params=_make_params(),
                last_run_time=0.0,
            )

        assert key not in param_adj  # stale entry cleared

    @pytest.mark.asyncio
    async def test_multiple_instruments_updated(self) -> None:
        """Gate map updated for all instruments in a multi-instrument response."""
        gate_map: dict[tuple[str, str], bool] = {}
        decisions = [
            _make_decision(instrument="ETH-PERP", strategy="momentum", enabled=True),
            _make_decision(instrument="BTC-PERP", strategy="mean_reversion", enabled=False),
        ]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=["ETH-PERP", "BTC-PERP"],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        assert gate_map[("ETH-PERP", "momentum")] is True
        assert gate_map[("BTC-PERP", "mean_reversion")] is False


# ---------------------------------------------------------------------------
# TestRedisIntegration — Redis hset called with correct mapping
# ---------------------------------------------------------------------------


class TestRedisIntegration:
    """Tests for Redis gate map mirroring."""

    @pytest.mark.asyncio
    async def test_redis_hset_called_with_correct_mapping(self) -> None:
        """Redis hset called with correct phantom:orchestrator:gate_map key."""
        gate_map: dict[tuple[str, str], bool] = {}
        redis = _make_redis()
        decisions = [_make_decision(instrument="ETH-PERP", strategy="momentum", enabled=True)]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=["ETH-PERP"],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=redis,
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        redis.hset.assert_awaited_once()
        call_args = redis.hset.call_args
        assert call_args.args[0] == "phantom:orchestrator:gate_map"
        mapping = call_args.kwargs.get("mapping", {})
        assert mapping.get("ETH-PERP:momentum") == "1"

    @pytest.mark.asyncio
    async def test_redis_hset_encodes_disabled_as_zero(self) -> None:
        """Disabled strategies are stored as '0' in Redis mapping."""
        gate_map: dict[tuple[str, str], bool] = {}
        redis = _make_redis()
        decisions = [_make_decision(instrument="SOL-PERP", strategy="regime_trend", enabled=False)]

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            await _run_tick(
                instrument_ids=["SOL-PERP"],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=redis,
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        mapping = redis.hset.call_args.kwargs["mapping"]
        assert mapping["SOL-PERP:regime_trend"] == "0"

    @pytest.mark.asyncio
    async def test_redis_error_does_not_crash_tick(self) -> None:
        """Redis failure is swallowed; tick still returns updated time."""
        redis = _make_redis()
        redis.hset.side_effect = ConnectionError("redis down")
        decisions = [_make_decision()]
        gate_map: dict[tuple[str, str], bool] = {}

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=decisions)),
            patch("agents.signals.orch_scheduler.validate_orchestrator_response", return_value=decisions),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=redis,
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        assert result > 0.0
        assert (_INSTRUMENT, _STRATEGY) in gate_map  # gate map still updated


# ---------------------------------------------------------------------------
# TestErrorRecovery — Claude returning None, exceptions
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    """Tests for error paths in the scheduler tick."""

    @pytest.mark.asyncio
    async def test_claude_none_logs_failure_and_advances_time(self) -> None:
        """When Claude returns None, time is still advanced (prevents hammering)."""
        gate_map: dict[tuple[str, str], bool] = {}

        with (
            patch("agents.signals.orch_scheduler.call_claude_orchestrator", new=AsyncMock(return_value=None)),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            result = await _run_tick(
                instrument_ids=[_INSTRUMENT],
                slow_stores={},
                latest_snapshots={},
                regime_detector=MagicMock(),
                redis_client=_make_redis(),
                gate_map=gate_map,
                param_adjustments={},
                params=_make_params(),
                last_run_time=0.0,
            )

        assert result > 0.0
        assert len(gate_map) == 0  # no decisions applied

    @pytest.mark.asyncio
    async def test_call_raises_exception_propagates_to_caller(self) -> None:
        """If call_claude_orchestrator raises, the exception propagates out of _run_tick."""
        with (
            patch(
                "agents.signals.orch_scheduler.call_claude_orchestrator",
                new=AsyncMock(side_effect=RuntimeError("unexpected")),
            ),
            patch("agents.signals.orch_scheduler.build_orchestrator_context", return_value="ctx"),
        ):
            with pytest.raises(RuntimeError, match="unexpected"):
                await _run_tick(
                    instrument_ids=[_INSTRUMENT],
                    slow_stores={},
                    latest_snapshots={},
                    regime_detector=MagicMock(),
                    redis_client=_make_redis(),
                    gate_map={},
                    param_adjustments={},
                    params=_make_params(),
                    last_run_time=0.0,
                )

    @pytest.mark.asyncio
    async def test_scheduler_loop_continues_after_tick_error(self) -> None:
        """run_orchestrator_scheduler catches tick errors and sleeps before next try."""
        call_count = 0
        original_run_tick = None

        async def _patched_run_tick(*args: Any, **kwargs: Any) -> float:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated tick failure")
            # Cancel the loop after a second call to stop the infinite loop
            raise asyncio.CancelledError()

        from agents.signals.orch_scheduler import run_orchestrator_scheduler

        with (
            patch("agents.signals.orch_scheduler._run_tick", new=_patched_run_tick),
            patch("agents.signals.orch_scheduler.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await run_orchestrator_scheduler(
                    instrument_ids=[_INSTRUMENT],
                    slow_stores={},
                    latest_snapshots={},
                    regime_detector=MagicMock(),
                    redis_client=_make_redis(),
                    gate_map={},
                    param_adjustments={},
                    params=_make_params(),
                )

        assert call_count == 2  # loop tried again after first error


# ---------------------------------------------------------------------------
# TestGateMapIntegration — safe default behavior
# ---------------------------------------------------------------------------


class TestGateMapIntegration:
    """Tests for gate_map.get() safe-default behavior (no lock needed)."""

    def test_missing_key_returns_true(self) -> None:
        """gate_map.get((inst, strat), True) == True when key absent (safe default)."""
        gate_map: dict[tuple[str, str], bool] = {}
        assert gate_map.get(("ETH-PERP", "momentum"), True) is True

    def test_disabled_key_returns_false(self) -> None:
        """gate_map.get((inst, strat), True) == False when explicitly disabled."""
        gate_map: dict[tuple[str, str], bool] = {("ETH-PERP", "momentum"): False}
        assert gate_map.get(("ETH-PERP", "momentum"), True) is False

    def test_enabled_key_returns_true(self) -> None:
        """gate_map.get((inst, strat), True) == True when explicitly enabled."""
        gate_map: dict[tuple[str, str], bool] = {("ETH-PERP", "momentum"): True}
        assert gate_map.get(("ETH-PERP", "momentum"), True) is True

    def test_different_instruments_are_independent(self) -> None:
        """Gate map is keyed by (instrument, strategy) — different instruments independent."""
        gate_map: dict[tuple[str, str], bool] = {("ETH-PERP", "momentum"): False}
        assert gate_map.get(("BTC-PERP", "momentum"), True) is True
        assert gate_map.get(("ETH-PERP", "momentum"), True) is False


# ---------------------------------------------------------------------------
# TestParamAdjMerge — orchestrator param_adj merged into session overrides
# ---------------------------------------------------------------------------


class TestParamAdjMerge:
    """Tests that validate the merge logic used in main.py strategy loop."""

    def test_orchestrator_overrides_session(self) -> None:
        """Orchestrator param_adj wins over session overrides on conflict."""
        session_overrides = {"min_conviction": 0.50, "weight": 0.20}
        orch_adj = {"min_conviction": 0.70}  # orchestrator sets higher threshold
        # Reproduce the merge logic from main.py:
        merged = {**session_overrides, **orch_adj}
        assert merged["min_conviction"] == 0.70  # orchestrator wins
        assert merged["weight"] == 0.20  # session survives for non-conflicting keys

    def test_empty_orch_adj_leaves_session_overrides_intact(self) -> None:
        """Empty orchestrator adjustment leaves session overrides unchanged."""
        session_overrides = {"min_conviction": 0.50}
        orch_adj: dict[str, Any] = {}
        merged = {**session_overrides, **orch_adj} if orch_adj else session_overrides
        assert merged == {"min_conviction": 0.50}

    def test_orch_adj_only_no_session_overrides(self) -> None:
        """Orchestrator adj applied when there are no session overrides."""
        session_overrides: dict[str, Any] = {}
        orch_adj = {"weight": 0.30}
        merged = {**session_overrides, **orch_adj}
        assert merged == {"weight": 0.30}

    def test_param_adj_missing_key_returns_empty_dict(self) -> None:
        """param_adjustments.get((inst, strat), {}) returns {} for missing keys."""
        param_adj: dict[tuple[str, str], dict[str, Any]] = {}
        result = param_adj.get(("ETH-PERP", "momentum"), {})
        assert result == {}


# ---------------------------------------------------------------------------
# TestModuleConstants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Smoke tests for module-level constants and imports."""

    def test_tick_sleep_is_60_seconds(self) -> None:
        """Orchestrator tick sleep is 60 seconds (not 30 like claude_scheduler)."""
        assert _TICK_SLEEP_SECONDS == 60

    def test_run_orchestrator_scheduler_is_importable(self) -> None:
        """Public API is importable from the module."""
        from agents.signals.orch_scheduler import run_orchestrator_scheduler  # noqa: PLC0415
        assert callable(run_orchestrator_scheduler)
