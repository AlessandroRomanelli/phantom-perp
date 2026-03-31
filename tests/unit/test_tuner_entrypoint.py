"""Unit tests for agents/tuner/entrypoint.py.

Tests cover:
- _bootstrap_volume: copies YAMLs to empty volume, no-op when populated
- _fetch_fills: calls TunerRepository.get_fills_by_strategy correctly
- main(): exits 0 on success, 1 on exception, calls setup_logging first
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# _bootstrap_volume tests
# ---------------------------------------------------------------------------


def test_bootstrap_volume_copies_yaml_to_empty_dir(tmp_path: Path) -> None:
    """_bootstrap_volume copies YAML files from image backup to empty strategies dir."""
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    image_dir = tmp_path / "_image_strategies"
    image_dir.mkdir()
    (image_dir / "momentum.yaml").write_text("momentum: {}")
    (image_dir / "mean_reversion.yaml").write_text("mean_reversion: {}")

    with (
        patch("agents.tuner.entrypoint.STRATEGIES_VOLUME", strategies_dir),
        patch("agents.tuner.entrypoint.IMAGE_STRATEGIES", image_dir),
    ):
        from agents.tuner.entrypoint import _bootstrap_volume

        _bootstrap_volume()

    yaml_files = list(strategies_dir.glob("*.yaml"))
    assert len(yaml_files) == 2
    file_names = {f.name for f in yaml_files}
    assert file_names == {"momentum.yaml", "mean_reversion.yaml"}


def test_bootstrap_volume_noop_when_yaml_exists(tmp_path: Path) -> None:
    """_bootstrap_volume skips copy when strategies dir already has YAML files."""
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    existing_yaml = strategies_dir / "existing.yaml"
    existing_yaml.write_text("existing: {}")

    image_dir = tmp_path / "_image_strategies"
    image_dir.mkdir()
    (image_dir / "new.yaml").write_text("new: {}")

    with (
        patch("agents.tuner.entrypoint.STRATEGIES_VOLUME", strategies_dir),
        patch("agents.tuner.entrypoint.IMAGE_STRATEGIES", image_dir),
    ):
        from agents.tuner.entrypoint import _bootstrap_volume

        _bootstrap_volume()

    yaml_files = list(strategies_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
    assert yaml_files[0].name == "existing.yaml"


# ---------------------------------------------------------------------------
# _fetch_fills tests
# ---------------------------------------------------------------------------


def test_fetch_fills_calls_repository_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fetch_fills calls TunerRepository.get_fills_by_strategy with correct args."""
    mock_fills = [MagicMock(), MagicMock()]

    mock_repo = MagicMock()
    mock_repo.get_fills_by_strategy = AsyncMock(return_value=mock_fills)

    mock_store = MagicMock()
    mock_repo_class = MagicMock(return_value=mock_repo)
    mock_store_class = MagicMock(return_value=mock_store)

    monkeypatch.setenv("DATABASE_URL", "postgresql://phantom:dev@localhost:5432/phantom_perp")

    with (
        patch("agents.tuner.entrypoint.RelationalStore", mock_store_class),
        patch("agents.tuner.entrypoint.TunerRepository", mock_repo_class),
    ):
        from agents.tuner.entrypoint import _fetch_fills

        result = _fetch_fills(lookback_days=14)

    mock_store_class.assert_called_once_with(
        "postgresql://phantom:dev@localhost:5432/phantom_perp"
    )
    mock_repo_class.assert_called_once_with(mock_store)
    mock_repo.get_fills_by_strategy.assert_called_once_with(
        route="autonomous", days=14
    )
    assert result == mock_fills


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


def test_main_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() exits with code 0 when run_tuning_cycle succeeds."""
    from libs.tuner.recommender import TuningResult

    mock_result = TuningResult(summary="all good", changes=[])
    mock_fills = []

    monkeypatch.setenv("DATABASE_URL", "postgresql://phantom:dev@localhost:5432/phantom_perp")

    with (
        patch("agents.tuner.entrypoint.setup_logging") as mock_logging,
        patch("agents.tuner.entrypoint._bootstrap_volume"),
        patch("agents.tuner.entrypoint._fetch_fills", return_value=mock_fills),
        patch("agents.tuner.entrypoint.run_tuning_cycle", return_value=mock_result),
    ):
        mock_logger = MagicMock()
        mock_logging.return_value = mock_logger

        from agents.tuner.entrypoint import main

        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0


def test_main_exits_one_on_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() exits with code 1 when run_tuning_cycle raises an exception."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://phantom:dev@localhost:5432/phantom_perp")

    with (
        patch("agents.tuner.entrypoint.setup_logging") as mock_logging,
        patch("agents.tuner.entrypoint._bootstrap_volume"),
        patch("agents.tuner.entrypoint._fetch_fills", return_value=[]),
        patch("agents.tuner.entrypoint.run_tuning_cycle", side_effect=ValueError("boom")),
    ):
        mock_logger = MagicMock()
        mock_logging.return_value = mock_logger

        from agents.tuner.entrypoint import main

        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 1


def test_main_calls_setup_logging_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() calls setup_logging('tuner') before any other work."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://phantom:dev@localhost:5432/phantom_perp")

    call_order: list[str] = []

    def record_logging(name: str, **kwargs: object) -> MagicMock:
        call_order.append("setup_logging")
        return MagicMock()

    def record_bootstrap() -> None:
        call_order.append("bootstrap")

    def record_fetch(lookback_days: int) -> list[object]:
        call_order.append("fetch")
        return []

    from libs.tuner.recommender import TuningResult

    mock_result = TuningResult(summary="", changes=[])

    with (
        patch("agents.tuner.entrypoint.setup_logging", side_effect=record_logging),
        patch("agents.tuner.entrypoint._bootstrap_volume", side_effect=record_bootstrap),
        patch("agents.tuner.entrypoint._fetch_fills", side_effect=record_fetch),
        patch("agents.tuner.entrypoint.run_tuning_cycle", return_value=mock_result),
    ):
        from agents.tuner.entrypoint import main

        with pytest.raises(SystemExit):
            main()

    assert call_order[0] == "setup_logging"
