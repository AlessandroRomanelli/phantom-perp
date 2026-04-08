"""Execution agent configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    instrument_limit_offset_bps: dict[str, int] = field(default_factory=dict)
    sl_limit_buffer_bps: int = 10

    def resolve_limit_offset_bps(self, instrument: str) -> int:
        """Return per-instrument limit offset bps, falling back to the global default.

        Args:
            instrument: Instrument symbol, e.g. "BTC-PERP".

        Returns:
            Configured bps offset for the instrument, or the global limit_offset_bps.
        """
        return self.instrument_limit_offset_bps.get(instrument, self.limit_offset_bps)


def load_execution_config(yaml_config: dict[str, Any]) -> ExecutionConfig:
    """Build an ExecutionConfig from the parsed default.yaml dict."""
    section = yaml_config.get("execution", {})
    if not section:
        return ExecutionConfig()

    # Parse per-instrument limit_offset_bps overrides.
    instruments_raw: dict[str, Any] = section.get("instruments", {}) or {}
    instrument_limit_offset_bps: dict[str, int] = {
        instrument_id: int(inst_cfg["limit_offset_bps"])
        for instrument_id, inst_cfg in instruments_raw.items()
        if isinstance(inst_cfg, dict) and "limit_offset_bps" in inst_cfg
    }

    return ExecutionConfig(
        default_order_type=section.get("default_order_type", "limit"),
        limit_offset_bps=int(section.get("limit_offset_bps", 5)),
        order_ttl=timedelta(seconds=section.get("order_ttl_seconds", 120)),
        max_slippage_bps=int(section.get("max_slippage_bps", 20)),
        retry_on_rejection=section.get("retry_on_rejection", True),
        max_retries=int(section.get("max_retries", 2)),
        prefer_maker=section.get("prefer_maker", True),
        instrument_limit_offset_bps=instrument_limit_offset_bps,
        sl_limit_buffer_bps=int(section.get("sl_limit_buffer_bps", 10)),
    )
