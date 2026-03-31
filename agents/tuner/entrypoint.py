"""Tuner container entrypoint.

Run-to-completion script that fetches fills from PostgreSQL, calls
run_tuning_cycle(), handles volume bootstrap, sends a Telegram
notification, and exits with the appropriate exit code (0 on success,
1 on any exception).

Usage:
    python -m agents.tuner.entrypoint
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import structlog

from libs.common.logging import setup_logging
from libs.storage.relational import RelationalStore
from libs.storage.repository import AttributedFill, TunerRepository
from libs.tuner import run_tuning_cycle
from libs.tuner.notifier import TunerNotifier
from libs.tuner.recommender import TuningResult

# Paths used inside the container. IMAGE_STRATEGIES holds the backup of
# strategy YAMLs baked into the image for first-run volume bootstrap (D-03).
CONFIG_DIR = Path("/app/configs")
STRATEGIES_VOLUME = Path("/app/configs/strategies")
BOUNDS_PATH = CONFIG_DIR / "bounds.yaml"
IMAGE_STRATEGIES = Path("/app/configs/_image_strategies")

DEFAULT_LOOKBACK_DAYS = 14

_logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def _bootstrap_volume() -> None:
    """Bootstrap the strategies volume on first run (D-03).

    Checks whether STRATEGIES_VOLUME already contains YAML files.
    If empty, copies all *.yaml from IMAGE_STRATEGIES to give the
    named volume its initial contents. If populated, does nothing.
    """
    existing = list(STRATEGIES_VOLUME.glob("*.yaml"))
    if existing:
        _logger.info("tuner_volume_ready", count=len(existing))
        return

    source_files = list(IMAGE_STRATEGIES.glob("*.yaml"))
    for src in source_files:
        shutil.copy2(src, STRATEGIES_VOLUME / src.name)

    _logger.info("tuner_volume_bootstrapped", count=len(source_files))


def _fetch_fills(lookback_days: int) -> list[AttributedFill]:
    """Fetch attributed fills from PostgreSQL (D-11).

    Bridges the sync entrypoint to the async repository layer by using
    asyncio.run(). Creates a RelationalStore and TunerRepository from
    the DATABASE_URL environment variable.

    Args:
        lookback_days: Rolling window to look back when querying fills.

    Returns:
        List of AttributedFill records from the database.
    """
    database_url = os.environ["DATABASE_URL"]
    store = RelationalStore(database_url)
    repo = TunerRepository(store)
    return asyncio.run(
        repo.get_fills_by_strategy(route="autonomous", days=lookback_days)
    )


async def _notify(result: TuningResult) -> None:
    """Send a Telegram notification with the tuning run result.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
    Errors are logged but never propagated — the tuner must complete
    regardless of Telegram availability.

    Args:
        result: The TuningResult from the completed tuning cycle.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    notifier = TunerNotifier(token=token, chat_id=chat_id)
    await notifier.send(result)


def main() -> None:
    """Tuner container entry point.

    Runs setup_logging first, then orchestrates bootstrap, fill fetch,
    the full tuning cycle, and Telegram notification. Exits 0 on success,
    1 on any exception.
    """
    logger = setup_logging("tuner")
    logger.info("tuner_starting")

    try:
        lookback_days = int(os.environ.get("TUNER_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)))
        model = os.environ.get("TUNER_MODEL")

        _bootstrap_volume()
        fills = _fetch_fills(lookback_days)
        logger.info("tuner_fills_fetched", count=len(fills))

        result = run_tuning_cycle(fills, config_dir=CONFIG_DIR, bounds_path=BOUNDS_PATH, model=model)
        logger.info("tuner_completed", changes=len(result.changes), summary=result.summary[:200])

        # Send Telegram notification (errors logged, never propagate)
        try:
            asyncio.run(_notify(result))
        except Exception as notify_exc:
            logger.warning(
                "tuner_notify_failed",
                error=str(notify_exc),
                exc_type=type(notify_exc).__name__,
            )

        sys.exit(0)

    except Exception as exc:
        logger.error("tuner_failed", error=str(exc), exc_type=type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
