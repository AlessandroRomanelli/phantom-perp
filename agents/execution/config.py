"""Execution agent configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """All execution-agent settings."""

    default_order_type: str = "limit"
    limit_offset_bps: int = 5
    order_ttl: timedelta = timedelta(seconds=120)
    max_slippage_bps: int = 20
    retry_on_rejection: bool = True
    max_retries: int = 2
    prefer_maker: bool = True


def load_execution_config(yaml_config: dict[str, Any]) -> ExecutionConfig:
    """Build an ExecutionConfig from the parsed default.yaml dict."""
    section = yaml_config.get("execution", {})
    if not section:
        return ExecutionConfig()

    return ExecutionConfig(
        default_order_type=section.get("default_order_type", "limit"),
        limit_offset_bps=int(section.get("limit_offset_bps", 5)),
        order_ttl=timedelta(seconds=section.get("order_ttl_seconds", 120)),
        max_slippage_bps=int(section.get("max_slippage_bps", 20)),
        retry_on_rejection=section.get("retry_on_rejection", True),
        max_retries=int(section.get("max_retries", 2)),
        prefer_maker=section.get("prefer_maker", True),
    )
