"""Tests for the regime field on MarketSnapshot."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from libs.common.models.enums import MarketRegime
from libs.common.models.market_snapshot import MarketSnapshot


def _make_snapshot(**kwargs: object) -> MarketSnapshot:
    """Build a minimal MarketSnapshot for tests."""
    defaults: dict[str, object] = dict(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        instrument="ETH-PERP",
        mark_price=Decimal("2000"),
        index_price=Decimal("2000"),
        last_price=Decimal("2000"),
        best_bid=Decimal("1999"),
        best_ask=Decimal("2001"),
        spread_bps=1.0,
        volume_24h=Decimal("1000"),
        open_interest=Decimal("5000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.1,
        volatility_1h=0.02,
        volatility_24h=0.03,
    )
    defaults.update(kwargs)
    return MarketSnapshot(**defaults)  # type: ignore[arg-type]


def test_regime_default_none() -> None:
    """MarketSnapshot without regime argument has regime == None."""
    snapshot = _make_snapshot()
    assert snapshot.regime is None


def test_regime_set_explicitly() -> None:
    """MarketSnapshot with regime=MarketRegime.TRENDING_UP has regime correctly set."""
    snapshot = _make_snapshot(regime=MarketRegime.TRENDING_UP)
    assert snapshot.regime == MarketRegime.TRENDING_UP


def test_replace_regime() -> None:
    """dataclasses.replace() can update regime on a snapshot."""
    snapshot = _make_snapshot()
    updated = replace(snapshot, regime=MarketRegime.SQUEEZE)
    assert updated.regime == MarketRegime.SQUEEZE
    # Original is unchanged (frozen)
    assert snapshot.regime is None


def test_backward_compat_no_regime_arg() -> None:
    """Existing snapshot construction without regime arg still works."""
    snapshot = _make_snapshot(mark_price=Decimal("3000"))
    assert snapshot.mark_price == Decimal("3000")
    assert snapshot.regime is None
