"""Instrument configuration registry.

Loads per-instrument metadata (tick size, min order size, etc.) from YAML
config and provides lookup by instrument ID. Populated at startup via
``load_instruments()`` called from ``get_settings()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class InstrumentConfig:
    """Immutable configuration for a single perpetual contract instrument.

    Attributes:
        id: Instrument identifier (e.g., "ETH-PERP").
        base_currency: Base asset symbol (e.g., "ETH").
        quote_currency: Quote asset symbol (e.g., "USDC").
        tick_size: Minimum price increment.
        min_order_size: Minimum order size in base currency.
    """

    id: str
    base_currency: str
    quote_currency: str
    tick_size: Decimal
    min_order_size: Decimal

    @property
    def ws_product_id(self) -> str:
        """WebSocket product ID used by Coinbase INTX feeds.

        Returns:
            String like "ETH-PERP-INTX".
        """
        return f"{self.id}-INTX"


_registry: dict[str, InstrumentConfig] = {}


def load_instruments(yaml_config: dict[str, Any]) -> None:
    """Load instrument configs from parsed YAML into the module registry.

    Clears any previously loaded instruments and repopulates from the
    ``instruments`` list in the provided config dict. YAML float values
    are converted to Decimal via string intermediate to avoid precision loss.

    Args:
        yaml_config: Parsed YAML config dict containing an ``instruments`` list.
    """
    _registry.clear()
    for entry in yaml_config.get("instruments", []):
        config = InstrumentConfig(
            id=entry["id"],
            base_currency=entry["base_currency"],
            quote_currency=entry["quote_currency"],
            tick_size=Decimal(str(entry["tick_size"])),
            min_order_size=Decimal(str(entry["min_order_size"])),
        )
        _registry[config.id] = config


def get_instrument(instrument_id: str) -> InstrumentConfig:
    """Look up an instrument configuration by ID.

    Args:
        instrument_id: Instrument identifier (e.g., "ETH-PERP").

    Returns:
        The InstrumentConfig for the requested instrument.

    Raises:
        KeyError: If the instrument ID is not in the registry.
    """
    return _registry[instrument_id]


def get_all_instruments() -> list[InstrumentConfig]:
    """Return all loaded instrument configurations.

    Returns:
        List of all InstrumentConfig objects in the registry.
    """
    return list(_registry.values())


def get_active_instrument_ids() -> list[str]:
    """Return all active instrument IDs.

    Returns:
        List of instrument ID strings currently in the registry.
    """
    return list(_registry.keys())
