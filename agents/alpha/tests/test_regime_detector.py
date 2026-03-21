"""Tests for market regime detection."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import MarketRegime
from libs.common.models.market_snapshot import MarketSnapshot

from agents.alpha.regime_detector import RegimeDetector


def _snap(
    mark: float = 2000.0,
    vol_24h: float = 0.35,
    **overrides: object,
) -> MarketSnapshot:
    """Build a minimal MarketSnapshot for regime detection tests."""
    defaults = dict(
        timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        instrument="ETH-PERP",
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.5)),
        best_ask=Decimal(str(mark + 0.5)),
        spread_bps=0.5,
        volume_24h=Decimal("50000"),
        open_interest=Decimal("100000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=datetime(2025, 6, 15, 13, 0, 0, tzinfo=UTC),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=vol_24h / 2,
        volatility_24h=vol_24h,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)  # type: ignore[arg-type]


class TestRegimeDetector:
    def test_default_regime_is_ranging(self) -> None:
        det = RegimeDetector()
        assert det.current_regime == MarketRegime.RANGING

    def test_not_enough_data_stays_ranging(self) -> None:
        det = RegimeDetector(lookback=50)
        # Feed < 10 snapshots — not enough for classification
        for _ in range(5):
            det.update(_snap())
        assert det.current_regime == MarketRegime.RANGING

    def test_high_volatility_detected(self) -> None:
        det = RegimeDetector(high_vol_threshold=0.50)
        for i in range(15):
            det.update(_snap(mark=2000 + i, vol_24h=0.65))
        assert det.current_regime == MarketRegime.HIGH_VOLATILITY

    def test_low_volatility_detected(self) -> None:
        det = RegimeDetector(low_vol_threshold=0.20)
        # Prices vary enough to avoid squeeze detection
        for i in range(15):
            det.update(_snap(mark=2000 + (i % 3) * 10, vol_24h=0.10))
        assert det.current_regime == MarketRegime.LOW_VOLATILITY

    def test_squeeze_detected(self) -> None:
        det = RegimeDetector(
            low_vol_threshold=0.20,
            squeeze_range_pct=0.5,
        )
        # Low vol + very narrow price range → squeeze
        for i in range(15):
            det.update(_snap(mark=2000.0 + i * 0.01, vol_24h=0.05))
        assert det.current_regime == MarketRegime.SQUEEZE

    def test_trending_up_detected(self) -> None:
        det = RegimeDetector(
            lookback=20,
            trend_pct_threshold=0.3,
        )
        # Prices rising steadily, normal vol
        for i in range(20):
            det.update(_snap(mark=2000 + i * 2, vol_24h=0.35))
        assert det.current_regime == MarketRegime.TRENDING_UP

    def test_trending_down_detected(self) -> None:
        det = RegimeDetector(
            lookback=20,
            trend_pct_threshold=0.3,
        )
        # Prices falling steadily, normal vol
        for i in range(20):
            det.update(_snap(mark=2000 - i * 2, vol_24h=0.35))
        assert det.current_regime == MarketRegime.TRENDING_DOWN

    def test_ranging_on_flat_prices(self) -> None:
        det = RegimeDetector(
            lookback=20,
            trend_pct_threshold=0.3,
        )
        # Oscillating around 2000, normal vol
        for i in range(20):
            det.update(_snap(mark=2000 + (i % 2) * 2 - 1, vol_24h=0.35))
        assert det.current_regime == MarketRegime.RANGING

    def test_regime_transitions(self) -> None:
        det = RegimeDetector(
            lookback=15,
            high_vol_threshold=0.50,
            low_vol_threshold=0.20,
            trend_pct_threshold=0.3,
        )
        # Start ranging
        for i in range(15):
            det.update(_snap(mark=2000, vol_24h=0.35))
        assert det.current_regime == MarketRegime.RANGING

        # Transition to high vol
        for i in range(15):
            det.update(_snap(mark=2000, vol_24h=0.65))
        assert det.current_regime == MarketRegime.HIGH_VOLATILITY

        # Transition to low vol
        for i in range(15):
            det.update(_snap(mark=2000 + i * 0.01, vol_24h=0.10))
        assert det.current_regime in (
            MarketRegime.LOW_VOLATILITY,
            MarketRegime.SQUEEZE,
        )
