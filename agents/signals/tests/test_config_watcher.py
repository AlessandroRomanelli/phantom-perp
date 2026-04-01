"""Tests for the config hot-reload watcher."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.signals.config_watcher import (
    _collect_mtimes,
    run_config_watcher,
)


class _FakeStrategy:
    """Minimal strategy stub for testing."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name


# ---------------------------------------------------------------------------
# _collect_mtimes
# ---------------------------------------------------------------------------


class TestCollectMtimes:
    def test_returns_dict_of_paths_and_floats(self) -> None:
        mtimes = _collect_mtimes()
        assert isinstance(mtimes, dict)
        for path, mtime in mtimes.items():
            assert isinstance(path, str)
            assert isinstance(mtime, float)

    def test_includes_strategy_yamls(self) -> None:
        mtimes = _collect_mtimes()
        yaml_files = [p for p in mtimes if "strategies/" in p and p.endswith(".yaml")]
        assert len(yaml_files) > 0

    def test_includes_matrix(self) -> None:
        mtimes = _collect_mtimes()
        matrix_files = [p for p in mtimes if "strategy_matrix.yaml" in p]
        assert len(matrix_files) == 1


# ---------------------------------------------------------------------------
# run_config_watcher
# ---------------------------------------------------------------------------


class TestRunConfigWatcher:
    @pytest.mark.asyncio
    async def test_no_change_does_not_rebuild(self) -> None:
        """When mtimes are unchanged, build_fn is never called."""
        strategies: dict[str, list[Any]] = {"ETH-PERP": [_FakeStrategy("mom")]}
        build_fn = MagicMock(return_value=[_FakeStrategy("mom")])

        task = asyncio.create_task(
            run_config_watcher(
                instrument_ids=["ETH-PERP"],
                strategies_by_instrument=strategies,
                build_fn=build_fn,
                poll_interval=1,
            )
        )
        # Let 2 ticks elapse — no file changes → no rebuild
        await asyncio.sleep(2.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        build_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_mtime_change_triggers_rebuild(self) -> None:
        """When a config file mtime changes, strategies are rebuilt."""
        strategies: dict[str, list[Any]] = {"ETH-PERP": [_FakeStrategy("mom")]}
        new_strats = [_FakeStrategy("mom_v2")]
        build_fn = MagicMock(return_value=new_strats)

        # Use a temp file to simulate a mtime change
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            tmp_path = Path(f.name)
            f.write(b"test: true\n")

        original_mtime = tmp_path.stat().st_mtime

        # Patch _collect_mtimes to use our temp file
        call_count = 0

        def fake_mtimes() -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # First call (startup snapshot): original mtime
                return {str(tmp_path): original_mtime}
            # Subsequent calls: bumped mtime (simulates tuner write)
            return {str(tmp_path): original_mtime + 10.0}

        with patch("agents.signals.config_watcher._collect_mtimes", side_effect=fake_mtimes):
            task = asyncio.create_task(
                run_config_watcher(
                    instrument_ids=["ETH-PERP"],
                    strategies_by_instrument=strategies,
                    build_fn=build_fn,
                    poll_interval=1,
                )
            )
            await asyncio.sleep(2.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # build_fn should have been called for the instrument
        build_fn.assert_called_once_with("ETH-PERP")
        # strategies_by_instrument should be updated in-place
        assert strategies["ETH-PERP"] is new_strats

        # Cleanup
        tmp_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_wire_callbacks_invoked_on_reload(self) -> None:
        """wire_claude_fn and wire_heatmap_fn are called for each instrument."""
        strategies: dict[str, list[Any]] = {"ETH-PERP": []}
        new_strats = [_FakeStrategy("claude")]
        build_fn = MagicMock(return_value=new_strats)
        wire_claude = MagicMock()
        wire_heatmap = MagicMock()

        call_count = 0

        def fake_mtimes() -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            return {"fake.yaml": 1.0 if call_count <= 1 else 2.0}

        with patch("agents.signals.config_watcher._collect_mtimes", side_effect=fake_mtimes):
            task = asyncio.create_task(
                run_config_watcher(
                    instrument_ids=["ETH-PERP"],
                    strategies_by_instrument=strategies,
                    build_fn=build_fn,
                    wire_claude_fn=wire_claude,
                    wire_heatmap_fn=wire_heatmap,
                    poll_interval=1,
                )
            )
            await asyncio.sleep(2.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        wire_claude.assert_called_once_with("ETH-PERP", new_strats)
        wire_heatmap.assert_called_once_with("ETH-PERP", new_strats)

    @pytest.mark.asyncio
    async def test_build_error_does_not_crash_watcher(self) -> None:
        """If build_fn raises for one instrument, others still reload."""
        strategies: dict[str, list[Any]] = {
            "ETH-PERP": [_FakeStrategy("mom")],
            "BTC-PERP": [_FakeStrategy("mom")],
        }
        btc_strats = [_FakeStrategy("mom_v2")]

        def build_fn(iid: str) -> list[Any]:
            if iid == "ETH-PERP":
                raise ValueError("bad YAML")
            return btc_strats

        call_count = 0

        def fake_mtimes() -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            return {"fake.yaml": 1.0 if call_count <= 1 else 2.0}

        with patch("agents.signals.config_watcher._collect_mtimes", side_effect=fake_mtimes):
            task = asyncio.create_task(
                run_config_watcher(
                    instrument_ids=["ETH-PERP", "BTC-PERP"],
                    strategies_by_instrument=strategies,
                    build_fn=build_fn,
                    poll_interval=1,
                )
            )
            await asyncio.sleep(2.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # ETH-PERP should keep old strategies (build_fn failed)
        assert strategies["ETH-PERP"][0].name == "mom"
        # BTC-PERP should have new strategies
        assert strategies["BTC-PERP"] is btc_strats
