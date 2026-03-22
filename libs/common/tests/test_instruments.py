"""Tests for InstrumentConfig registry."""

from __future__ import annotations

from decimal import Decimal

import pytest

from libs.common.instruments import (
    InstrumentConfig,
    get_active_instrument_ids,
    get_all_instruments,
    get_instrument,
    load_instruments,
)

SAMPLE_YAML = {
    "instruments": [
        {
            "id": "ETH-PERP",
            "base_currency": "ETH",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.0001,
        },
        {
            "id": "BTC-PERP",
            "base_currency": "BTC",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.00001,
        },
        {
            "id": "SOL-PERP",
            "base_currency": "SOL",
            "quote_currency": "USDC",
            "tick_size": 0.001,
            "min_order_size": 0.01,
        },
        {
            "id": "QQQ-PERP",
            "base_currency": "QQQ",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.001,
        },
        {
            "id": "SPY-PERP",
            "base_currency": "SPY",
            "quote_currency": "USDC",
            "tick_size": 0.01,
            "min_order_size": 0.001,
        },
    ]
}


@pytest.fixture(autouse=True)
def _load_sample_instruments() -> None:
    """Load sample instruments before each test."""
    load_instruments(SAMPLE_YAML)


def test_load_instruments_populates_registry_with_five_entries() -> None:
    """load_instruments() with valid YAML dict containing 5 instruments populates registry."""
    instruments = get_all_instruments()
    assert len(instruments) == 5


def test_get_instrument_returns_correct_config() -> None:
    """get_instrument('ETH-PERP') returns InstrumentConfig with correct fields."""
    cfg = get_instrument("ETH-PERP")
    assert cfg.id == "ETH-PERP"
    assert cfg.base_currency == "ETH"
    assert cfg.quote_currency == "USDC"
    assert cfg.tick_size == Decimal("0.01")
    assert cfg.min_order_size == Decimal("0.0001")


def test_get_instrument_nonexistent_raises_key_error() -> None:
    """get_instrument('NONEXISTENT') raises KeyError."""
    with pytest.raises(KeyError):
        get_instrument("NONEXISTENT")


def test_get_all_instruments_returns_all_configs() -> None:
    """get_all_instruments() returns list of all 5 InstrumentConfig objects."""
    instruments = get_all_instruments()
    assert len(instruments) == 5
    assert all(isinstance(i, InstrumentConfig) for i in instruments)
    ids = {i.id for i in instruments}
    assert ids == {"ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"}


def test_get_active_instrument_ids_returns_all_ids() -> None:
    """get_active_instrument_ids() returns list of 5 instrument ID strings."""
    ids = get_active_instrument_ids()
    assert len(ids) == 5
    assert "ETH-PERP" in ids
    assert "BTC-PERP" in ids
    assert "SOL-PERP" in ids
    assert "QQQ-PERP" in ids
    assert "SPY-PERP" in ids


def test_ws_product_id_property() -> None:
    """InstrumentConfig.ws_product_id returns '{id}-INTX'."""
    cfg = get_instrument("ETH-PERP")
    assert cfg.ws_product_id == "ETH-PERP-INTX"

    cfg_btc = get_instrument("BTC-PERP")
    assert cfg_btc.ws_product_id == "BTC-PERP-INTX"


def test_tick_size_and_min_order_size_are_decimal() -> None:
    """tick_size and min_order_size are Decimal even when YAML provides floats."""
    cfg = get_instrument("ETH-PERP")
    assert isinstance(cfg.tick_size, Decimal)
    assert isinstance(cfg.min_order_size, Decimal)

    cfg_sol = get_instrument("SOL-PERP")
    assert isinstance(cfg_sol.tick_size, Decimal)
    assert cfg_sol.tick_size == Decimal("0.001")
