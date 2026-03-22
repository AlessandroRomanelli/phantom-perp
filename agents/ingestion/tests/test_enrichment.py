"""Tests for enrichment (derived field computation)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.coinbase.models import CandleResponse

from agents.ingestion.enrichment import (
    compute_orderbook_imbalance,
    compute_spread_bps,
    compute_volatility_1h,
    compute_volatility_24h,
    compute_volatility_from_candles,
)
from agents.ingestion.state import BookLevel, IngestionState


class TestComputeSpreadBps:
    def test_normal_spread(self) -> None:
        bps = compute_spread_bps(Decimal("2230.00"), Decimal("2231.00"))
        # spread = 1.0, mid = 2230.50, bps = 1.0/2230.50 * 10000 ≈ 4.48
        assert 4.0 < bps < 5.0

    def test_zero_spread(self) -> None:
        bps = compute_spread_bps(Decimal("2230.00"), Decimal("2230.00"))
        assert bps == 0.0

    def test_wide_spread(self) -> None:
        bps = compute_spread_bps(Decimal("2200.00"), Decimal("2300.00"))
        # spread = 100, mid = 2250, bps = 100/2250 * 10000 ≈ 444.44
        assert 440.0 < bps < 450.0

    def test_zero_mid_returns_zero(self) -> None:
        bps = compute_spread_bps(Decimal("0"), Decimal("0"))
        assert bps == 0.0


class TestComputeOrderbookImbalance:
    def test_balanced_book(self) -> None:
        bids = [BookLevel(Decimal("100"), Decimal("10"))]
        asks = [BookLevel(Decimal("101"), Decimal("10"))]
        imb = compute_orderbook_imbalance(bids, asks)
        assert imb == pytest.approx(0.0)

    def test_bid_heavy(self) -> None:
        bids = [BookLevel(Decimal("100"), Decimal("90"))]
        asks = [BookLevel(Decimal("101"), Decimal("10"))]
        imb = compute_orderbook_imbalance(bids, asks)
        # (90-10)/(90+10) = 0.8
        assert imb == pytest.approx(0.8)

    def test_ask_heavy(self) -> None:
        bids = [BookLevel(Decimal("100"), Decimal("10"))]
        asks = [BookLevel(Decimal("101"), Decimal("90"))]
        imb = compute_orderbook_imbalance(bids, asks)
        assert imb == pytest.approx(-0.8)

    def test_empty_book(self) -> None:
        assert compute_orderbook_imbalance([], []) == 0.0

    def test_depth_limit(self) -> None:
        bids = [BookLevel(Decimal("100"), Decimal("10")) for _ in range(20)]
        asks = [BookLevel(Decimal("101"), Decimal("10")) for _ in range(20)]
        imb = compute_orderbook_imbalance(bids, asks, depth=5)
        assert imb == pytest.approx(0.0)

    def test_unequal_depths(self) -> None:
        bids = [BookLevel(Decimal("100"), Decimal("10")) for _ in range(3)]
        asks = [BookLevel(Decimal("101"), Decimal("10")) for _ in range(1)]
        imb = compute_orderbook_imbalance(bids, asks, depth=10)
        # bid_vol=30, ask_vol=10, imb = (30-10)/40 = 0.5
        assert imb == pytest.approx(0.5)


class TestComputeVolatility:
    def _make_candles(self, closes: list[float]) -> list[CandleResponse]:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        return [
            CandleResponse(
                start=base + timedelta(minutes=i),
                open=Decimal(str(c)),
                high=Decimal(str(c + 1)),
                low=Decimal(str(c - 1)),
                close=Decimal(str(c)),
                volume=Decimal("100"),
            )
            for i, c in enumerate(closes)
        ]

    def test_constant_prices_zero_vol(self) -> None:
        candles = self._make_candles([100.0] * 24)
        vol = compute_volatility_from_candles(candles)
        assert vol == 0.0

    def test_increasing_prices_positive_vol(self) -> None:
        prices = [2000.0 + i * 10 for i in range(24)]
        candles = self._make_candles(prices)
        vol = compute_volatility_from_candles(candles)
        assert vol > 0.0

    def test_insufficient_data(self) -> None:
        candles = self._make_candles([100.0])
        vol = compute_volatility_from_candles(candles)
        assert vol == 0.0

    def test_empty_candles(self) -> None:
        assert compute_volatility_from_candles([]) == 0.0

    def test_periods_param_limits_data(self) -> None:
        prices = [2000.0 + i * 10 for i in range(100)]
        candles = self._make_candles(prices)
        vol_10 = compute_volatility_from_candles(candles, periods=10)
        vol_100 = compute_volatility_from_candles(candles, periods=100)
        # Both should be positive; exact values differ
        assert vol_10 > 0.0
        assert vol_100 > 0.0

    def test_volatility_1h_uses_one_minute_candles(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        prices = [2000.0 + i * 0.5 for i in range(60)]
        state.candles_by_granularity["ONE_MINUTE"] = self._make_candles(prices)
        vol = compute_volatility_1h(state)
        assert vol > 0.0

    def test_volatility_24h_uses_one_hour_candles(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        prices = [2000.0 + i * 5 for i in range(24)]
        state.candles_by_granularity["ONE_HOUR"] = self._make_candles(prices)
        vol = compute_volatility_24h(state)
        assert vol > 0.0

    def test_volatility_1h_no_candles(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        assert compute_volatility_1h(state) == 0.0

    def test_volatility_24h_no_candles(self) -> None:
        state = IngestionState(instrument_id="ETH-PERP")
        assert compute_volatility_24h(state) == 0.0
