"""Audit logging for tuner parameter changes.

Stub created in Task 1 to satisfy __init__.py imports.
Full implementation in Task 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ParameterChange:
    """Immutable record of a single parameter change recommendation.

    Args:
        strategy: Strategy name (e.g. "momentum").
        instrument: Instrument ID (e.g. "ETH-PERP") or None for base-level params.
        param: Parameter name (e.g. "min_conviction").
        old_value: Previous parameter value.
        new_value: Recommended new value.
        reasoning: Human-readable explanation from the tuner.
        timestamp: UTC timestamp when the change was recommended.
    """

    strategy: str
    instrument: str | None
    param: str
    old_value: float | int
    new_value: float | int
    reasoning: str
    timestamp: datetime


def log_parameter_change(change: ParameterChange) -> None:
    """Emit a structured audit log entry for a parameter change.

    Args:
        change: The ParameterChange record to log.
    """
    _logger.info(
        "tuner_parameter_changed",
        strategy=change.strategy,
        instrument=change.instrument,
        param=change.param,
        old_value=change.old_value,
        new_value=change.new_value,
        reasoning=change.reasoning,
        timestamp=change.timestamp.isoformat(),
    )


def log_no_change(
    strategy: str,
    instrument: str | None,
    reasoning: str,
) -> None:
    """Emit a structured audit log entry when no parameter change is made.

    Args:
        strategy: Strategy name.
        instrument: Instrument ID or None for base-level.
        reasoning: Human-readable explanation for why no change was made.
    """
    _logger.info(
        "tuner_no_change",
        strategy=strategy,
        instrument=instrument,
        reasoning=reasoning,
        timestamp=datetime.now(UTC).isoformat(),
    )
