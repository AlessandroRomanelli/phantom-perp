"""Hot-reload watcher for strategy YAML configs.

Runs a background coroutine that periodically checks whether strategy
config files on disk have changed (via mtime comparison).  When a change
is detected, all per-instrument strategy instances are rebuilt from the
updated YAML, and the shared ``strategies_by_instrument`` dict is updated
in-place.

Design notes:
- Uses file mtime (not content hashing) for speed — checking 13 files'
  stat() is negligible compared to SHA256 reads.
- Single-threaded asyncio — no locks needed since the reload replaces list
  references atomically and the main loop reads them on the next tick.
- Claude queues and heatmap stores are re-wired to new strategy instances
  automatically after rebuild.
- Errors during rebuild are caught per-instrument; a bad YAML for one
  instrument does not block others from reloading.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from agents.signals.strategies.base import SignalStrategy  # noqa: TC001

_logger = structlog.get_logger(__name__)

# How often to check for config changes (seconds).
_POLL_INTERVAL_SECONDS: int = 60

# Paths to watch — strategy YAMLs + matrix + bounds.
_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
_STRATEGY_DIR = _CONFIGS_DIR / "strategies"
_MATRIX_PATH = _CONFIGS_DIR / "strategy_matrix.yaml"
_BOUNDS_PATH = _CONFIGS_DIR / "bounds.yaml"


def _collect_mtimes() -> dict[str, float]:
    """Collect mtime for all watched config files.

    Returns:
        Dict mapping file path (str) to mtime (float).
        Missing files are silently omitted.
    """
    mtimes: dict[str, float] = {}

    if _MATRIX_PATH.exists():
        mtimes[str(_MATRIX_PATH)] = _MATRIX_PATH.stat().st_mtime

    if _BOUNDS_PATH.exists():
        mtimes[str(_BOUNDS_PATH)] = _BOUNDS_PATH.stat().st_mtime

    if _STRATEGY_DIR.is_dir():
        for f in sorted(_STRATEGY_DIR.glob("*.yaml")):
            mtimes[str(f)] = f.stat().st_mtime

    return mtimes


async def run_config_watcher(
    instrument_ids: list[str],
    strategies_by_instrument: dict[str, list[SignalStrategy]],
    build_fn: Any,  # Callable[[str], list[SignalStrategy]]
    *,
    wire_claude_fn: Any | None = None,
    wire_heatmap_fn: Any | None = None,
    poll_interval: int = _POLL_INTERVAL_SECONDS,
) -> None:
    """Background task that watches strategy configs and hot-reloads on change.

    Args:
        instrument_ids: Active instrument IDs.
        strategies_by_instrument: Shared mutable dict of per-instrument strategy
            lists.  Updated in-place on reload.
        build_fn: ``build_strategies_for_instrument(instrument_id)`` callable
            that returns a fresh list of strategy instances from current YAML.
        wire_claude_fn: Optional callback ``(instrument_id, strategies)`` to
            re-wire Claude queues to new ClaudeMarketAnalysisStrategy instances.
        wire_heatmap_fn: Optional callback ``(instrument_id, strategies)`` to
            re-wire heatmap stores to new LiquidationCascadeStrategy instances.
        poll_interval: Seconds between mtime checks.
    """
    prev_mtimes = _collect_mtimes()

    _logger.info(
        "config_watcher_started",
        watched_files=len(prev_mtimes),
        poll_interval=poll_interval,
    )

    while True:
        await asyncio.sleep(poll_interval)

        try:
            current_mtimes = _collect_mtimes()
        except OSError as exc:
            _logger.warning("config_watcher_stat_error", error=str(exc))
            continue

        if current_mtimes == prev_mtimes:
            continue

        # Identify which files changed
        changed: list[str] = []
        for path, mtime in current_mtimes.items():
            if prev_mtimes.get(path) != mtime:
                changed.append(path)
        for path in prev_mtimes:
            if path not in current_mtimes:
                changed.append(path)

        _logger.info(
            "config_change_detected",
            changed_files=[Path(p).name for p in changed],
        )

        # Rebuild strategies for each instrument
        reloaded_count = 0
        for iid in instrument_ids:
            try:
                new_strategies = build_fn(iid)
                strategies_by_instrument[iid] = new_strategies

                # Re-wire Claude queues
                if wire_claude_fn is not None:
                    wire_claude_fn(iid, new_strategies)

                # Re-wire heatmap stores
                if wire_heatmap_fn is not None:
                    wire_heatmap_fn(iid, new_strategies)

                reloaded_count += 1
                _logger.info(
                    "instrument_strategies_reloaded",
                    instrument=iid,
                    strategies=[s.name for s in new_strategies],
                )
            except Exception as exc:
                _logger.error(
                    "config_reload_failed",
                    instrument=iid,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )

        _logger.info(
            "config_reload_complete",
            instruments_reloaded=reloaded_count,
            instruments_total=len(instrument_ids),
        )

        prev_mtimes = current_mtimes
