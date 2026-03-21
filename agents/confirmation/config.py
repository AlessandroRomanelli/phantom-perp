"""Confirmation agent configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class AutoApproveConfig:
    """Rules for bypassing Telegram confirmation on low-risk orders."""

    enabled: bool = False
    max_notional_usdc: Decimal = Decimal("2000")
    min_conviction: float = 0.9
    only_reduce: bool = False


@dataclass(frozen=True, slots=True)
class QuietHoursConfig:
    """Quiet-hours behaviour when the user is likely asleep."""

    enabled: bool = True
    start: time = time(23, 0)
    end: time = time(7, 0)
    timezone: str = "Europe/Zurich"
    behavior: str = "queue"  # "queue" or "reject"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True, slots=True)
class BatchingConfig:
    """Group orders arriving within a short window into one message."""

    enabled: bool = True
    window: timedelta = timedelta(seconds=30)
    max_batch_size: int = 5


@dataclass(frozen=True, slots=True)
class ConfirmationConfig:
    """All confirmation-agent settings."""

    default_ttl: timedelta = timedelta(seconds=300)
    stale_price_threshold_pct: float = 1.0
    auto_approve: AutoApproveConfig = AutoApproveConfig()
    quiet_hours: QuietHoursConfig = QuietHoursConfig()
    batching: BatchingConfig = BatchingConfig()


def load_confirmation_config(yaml_config: dict[str, Any]) -> ConfirmationConfig:
    """Build a ConfirmationConfig from the parsed default.yaml dict."""
    section = yaml_config.get("confirmation", {})
    if not section:
        return ConfirmationConfig()

    aa = section.get("auto_approve", {})
    qh = section.get("quiet_hours", {})
    bat = section.get("batching", {})

    auto_approve = AutoApproveConfig(
        enabled=aa.get("enabled", False),
        max_notional_usdc=Decimal(str(aa.get("max_notional_usdc", 2000))),
        min_conviction=float(aa.get("min_conviction", 0.9)),
        only_reduce=aa.get("only_reduce", False),
    )

    # Parse time strings like "23:00"
    start_parts = str(qh.get("start", "23:00")).split(":")
    end_parts = str(qh.get("end", "07:00")).split(":")
    quiet_hours = QuietHoursConfig(
        enabled=qh.get("enabled", True),
        start=time(int(start_parts[0]), int(start_parts[1])),
        end=time(int(end_parts[0]), int(end_parts[1])),
        timezone=qh.get("timezone", "Europe/Zurich"),
        behavior=qh.get("behavior", "queue"),
    )

    batching = BatchingConfig(
        enabled=bat.get("enabled", True),
        window=timedelta(seconds=bat.get("window_seconds", 30)),
        max_batch_size=bat.get("max_batch_size", 5),
    )

    return ConfirmationConfig(
        default_ttl=timedelta(seconds=section.get("default_ttl_seconds", 300)),
        stale_price_threshold_pct=float(
            section.get("stale_price_threshold_pct", 1.0),
        ),
        auto_approve=auto_approve,
        quiet_hours=quiet_hours,
        batching=batching,
    )
