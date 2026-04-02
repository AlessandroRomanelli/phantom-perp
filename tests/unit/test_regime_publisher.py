"""Unit tests for regime Redis mirroring and RegimeDetector.regimes property."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from agents.alpha.main import _mirror_regime_to_redis
from agents.alpha.regime_detector import RegimeDetector
from libs.common.models.enums import MarketRegime
from libs.common.models.market_snapshot import MarketSnapshot


def _make_snapshot(
    instrument: str = "ETH-PERP",
    price: float = 2500.0,
    vol: float = 0.35,
) -> MarketSnapshot:
    """Build a minimal MarketSnapshot for testing."""
    ts = datetime.now(tz=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument,
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 1)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.5)),
        best_ask=Decimal(str(price + 0.5)),
        spread_bps=4.0,
        volume_24h=Decimal("1000000"),
        open_interest=Decimal("500000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(hours=1),
        hours_since_last_funding=7.0,
        orderbook_imbalance=0.15,
        volatility_1h=vol,
        volatility_24h=vol,
    )


class TestMirrorRegimeToRedis:
    @pytest.mark.asyncio
    async def test_mirror_writes_regime_hash(self) -> None:
        """hset is called with phantom:regime and correct instrument→regime mapping."""
        redis_client = AsyncMock()
        regimes = {
            "ETH-PERP": MarketRegime.TRENDING_UP,
            "BTC-PERP": MarketRegime.RANGING,
        }

        await _mirror_regime_to_redis(regimes, redis_client)

        redis_client.hset.assert_awaited_once_with(
            "phantom:regime",
            mapping={
                "ETH-PERP": "trending_up",
                "BTC-PERP": "ranging",
            },
        )

    @pytest.mark.asyncio
    async def test_mirror_empty_regimes_skips_write(self) -> None:
        """Empty regimes dict results in no Redis call."""
        redis_client = AsyncMock()

        await _mirror_regime_to_redis({}, redis_client)

        redis_client.hset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mirror_redis_error_does_not_propagate(self) -> None:
        """ConnectionError raised by hset is swallowed — nothing propagates."""
        redis_client = AsyncMock()
        redis_client.hset.side_effect = ConnectionError("Redis down")

        # Must not raise
        await _mirror_regime_to_redis({"ETH-PERP": MarketRegime.HIGH_VOLATILITY}, redis_client)


class TestRegimesProperty:
    def test_regimes_property_returns_copy(self) -> None:
        """RegimeDetector.regimes returns a copy — mutations don't affect internal state."""
        detector = RegimeDetector()

        # Feed enough snapshots to register a regime for ETH-PERP
        for i in range(15):
            detector.update(_make_snapshot("ETH-PERP", price=2500.0 + i * 0.1))

        result = detector.regimes

        assert isinstance(result, dict)
        assert "ETH-PERP" in result
        assert isinstance(result["ETH-PERP"], MarketRegime)

        # Mutation of the returned copy must not affect internal state
        original_regime = detector.regimes["ETH-PERP"]
        result["ETH-PERP"] = MarketRegime.HIGH_VOLATILITY
        assert detector.regimes["ETH-PERP"] == original_regime

    def test_regimes_property_empty_before_updates(self) -> None:
        """Fresh RegimeDetector.regimes returns empty dict."""
        detector = RegimeDetector()
        assert detector.regimes == {}

    def test_regimes_property_multi_instrument(self) -> None:
        """regimes includes all instruments that have been updated."""
        detector = RegimeDetector()

        for i in range(15):
            detector.update(_make_snapshot("ETH-PERP", price=2500.0 + i * 0.1))
            detector.update(_make_snapshot("BTC-PERP", price=50000.0 + i * 1.0))

        result = detector.regimes
        assert "ETH-PERP" in result
        assert "BTC-PERP" in result
