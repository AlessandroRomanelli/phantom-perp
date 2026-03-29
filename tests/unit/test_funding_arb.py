"""Tests for the funding arbitrage strategy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.funding_arb import FundingArbParams, FundingArbStrategy
from libs.common.instruments import InstrumentConfig, _registry
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _register_eth_perp() -> None:
    """Ensure ETH-PERP is in the instrument registry for all tests."""
    _registry["ETH-PERP"] = InstrumentConfig(
        id="ETH-PERP",
        base_currency="ETH",
        quote_currency="USDC",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.001"),
    )
    yield  # type: ignore[misc]
    _registry.pop("ETH-PERP", None)


def _make_snapshot(
    price: float = 2500.0,
    funding_rate: float = 0.0003,
    hours_since_last_funding: float = 6.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """Build a minimal MarketSnapshot for testing."""
    if ts is None:
        ts = datetime.now(tz=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument="ETH-PERP",
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 1)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.5)),
        best_ask=Decimal(str(price + 0.5)),
        spread_bps=4.0,
        volume_24h=Decimal("1000000"),
        open_interest=Decimal("500000"),
        funding_rate=Decimal(str(funding_rate)),
        next_funding_time=ts + timedelta(hours=2),
        hours_since_last_funding=hours_since_last_funding,
        orderbook_imbalance=0.0,
        volatility_1h=0.02,
        volatility_24h=0.03,
    )


def _build_store_with_history(
    n_samples: int = 50,
    base_price: float = 2500.0,
    normal_funding: float = 0.0001,
    sample_interval_sec: int = 1,
) -> FeatureStore:
    """Create a FeatureStore pre-loaded with price and funding history.

    Funding rates are varied slightly around `normal_funding` so the
    FeatureStore accumulates multiple distinct funding rate entries
    (it deduplicates consecutive identical values). The z-score of an
    extreme rate against this window will be high.
    """
    store = FeatureStore(
        max_samples=500,
        sample_interval=timedelta(seconds=sample_interval_sec),
    )
    base_ts = datetime(2025, 6, 1, tzinfo=UTC)
    rng = np.random.default_rng(seed=42)
    for i in range(n_samples):
        # Small price variation for valid ATR computation
        price = base_price + (i % 10) * 0.5
        # Vary funding slightly so FeatureStore stores each unique value
        funding = normal_funding + rng.normal(0, normal_funding * 0.1)
        snap = _make_snapshot(
            price=price,
            funding_rate=round(funding, 10),
            ts=base_ts + timedelta(seconds=i * sample_interval_sec),
        )
        store.update(snap)
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFundingArbNoSignal:
    """Cases where the strategy should NOT fire."""

    def test_insufficient_funding_samples(self) -> None:
        """No signal when store has fewer funding samples than min_funding_samples."""
        params = FundingArbParams(min_funding_samples=20)
        strategy = FundingArbStrategy(params=params)
        # Only 5 samples — below the 20 minimum
        store = _build_store_with_history(n_samples=5)
        snap = _make_snapshot(funding_rate=0.001)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_insufficient_price_history(self) -> None:
        """No signal when store has fewer price samples than min_history."""
        strategy = FundingArbStrategy(params=FundingArbParams())
        store = FeatureStore(sample_interval=timedelta(seconds=1))
        # Only 3 samples — min_history is atr_period + 5 = 19
        base_ts = datetime(2025, 6, 1, tzinfo=UTC)
        for i in range(3):
            snap = _make_snapshot(
                funding_rate=0.001,
                ts=base_ts + timedelta(seconds=i),
            )
            store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_zscore_below_threshold(self) -> None:
        """No signal when funding z-score is below threshold."""
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        params = FundingArbParams(
            zscore_threshold=2.0,
            min_funding_samples=5,
            cooldown_bars=0,
        )
        strategy = FundingArbStrategy(params=params)
        # Funding rate close to the mean — z-score will be ~0
        snap = _make_snapshot(funding_rate=0.0001)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_annualized_rate_too_low(self) -> None:
        """No signal when z-score is extreme but annualized rate is below minimum."""
        # Use very small funding rates so annualized is low even at high z-score
        store = _build_store_with_history(
            n_samples=50, normal_funding=0.0000001,
        )
        params = FundingArbParams(
            zscore_threshold=1.5,
            min_annualized_rate_pct=10.0,
            min_funding_samples=5,
            cooldown_bars=0,
        )
        strategy = FundingArbStrategy(params=params)
        # Extreme z-score but tiny absolute rate
        snap = _make_snapshot(funding_rate=0.000005)
        store.update(snap)
        result = strategy.evaluate(snap, store)
        assert result == []

    def test_cooldown_enforced(self) -> None:
        """No signal during cooldown period after a signal fires."""
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        params = FundingArbParams(
            zscore_threshold=1.5,
            min_annualized_rate_pct=1.0,
            min_funding_samples=5,
            min_conviction=0.0,  # Accept any conviction
            cooldown_bars=5,
        )
        strategy = FundingArbStrategy(params=params)

        # First: trigger a signal with extreme funding
        snap = _make_snapshot(funding_rate=0.005, hours_since_last_funding=7.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1

        # Immediately try again — should be in cooldown
        ts2 = datetime.now(tz=UTC) + timedelta(seconds=1)
        snap2 = _make_snapshot(funding_rate=0.005, ts=ts2)
        store.update(snap2)
        assert strategy.evaluate(snap2, store) == []


class TestFundingArbSignals:
    """Cases where the strategy SHOULD fire."""

    def _make_strategy_and_store(
        self,
        normal_funding: float = 0.0001,
    ) -> tuple[FundingArbStrategy, FeatureStore]:
        """Create a strategy+store pair with permissive params for testing."""
        params = FundingArbParams(
            zscore_threshold=1.5,
            min_annualized_rate_pct=1.0,  # Low bar so we can control via funding rate
            min_conviction=0.0,  # Accept any conviction
            min_funding_samples=5,
            cooldown_bars=0,
        )
        store = _build_store_with_history(
            n_samples=50, normal_funding=normal_funding,
        )
        return FundingArbStrategy(params=params), store

    def test_short_on_extreme_positive_funding(self) -> None:
        """Positive funding → longs pay shorts → SHORT to collect."""
        strategy, store = self._make_strategy_and_store()
        snap = _make_snapshot(funding_rate=0.005, hours_since_last_funding=6.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.SHORT
        assert sig.source == SignalSource.FUNDING_ARB
        assert sig.instrument == "ETH-PERP"
        assert 0.0 < sig.conviction <= 1.0

    def test_long_on_extreme_negative_funding(self) -> None:
        """Negative funding → shorts pay longs → LONG to collect."""
        strategy, store = self._make_strategy_and_store()
        snap = _make_snapshot(funding_rate=-0.005, hours_since_last_funding=6.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.source == SignalSource.FUNDING_ARB

    def test_signal_has_correct_fields(self) -> None:
        """Verify all StandardSignal fields are populated correctly."""
        strategy, store = self._make_strategy_and_store()
        snap = _make_snapshot(funding_rate=0.005, hours_since_last_funding=7.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_id.startswith("sig-")
        assert sig.entry_price == snap.last_price
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.time_horizon == timedelta(hours=4)
        assert "funding_rate" in sig.metadata
        assert "z_score" in sig.metadata
        assert "annualized_pct" in sig.metadata

    def test_stop_loss_direction_long(self) -> None:
        """LONG signal: stop below entry, TP above entry."""
        strategy, store = self._make_strategy_and_store()
        snap = _make_snapshot(funding_rate=-0.005, hours_since_last_funding=6.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.stop_loss < sig.entry_price  # type: ignore[operator]
        assert sig.take_profit > sig.entry_price  # type: ignore[operator]

    def test_stop_loss_direction_short(self) -> None:
        """SHORT signal: stop above entry, TP below entry."""
        strategy, store = self._make_strategy_and_store()
        snap = _make_snapshot(funding_rate=0.005, hours_since_last_funding=6.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.SHORT
        assert sig.stop_loss > sig.entry_price  # type: ignore[operator]
        assert sig.take_profit < sig.entry_price  # type: ignore[operator]


class TestConviction:
    """Conviction computation tests."""

    def test_conviction_scales_with_zscore(self) -> None:
        """Higher z-score should produce higher conviction."""
        conv_low = FundingArbStrategy._compute_conviction(
            z_score=2.5, annualized_pct=20.0, hours_since_last_funding=4.0,
        )
        conv_high = FundingArbStrategy._compute_conviction(
            z_score=5.0, annualized_pct=20.0, hours_since_last_funding=4.0,
        )
        assert conv_high > conv_low

    def test_conviction_scales_with_rate(self) -> None:
        """Higher annualized rate should produce higher conviction."""
        conv_low = FundingArbStrategy._compute_conviction(
            z_score=3.0, annualized_pct=15.0, hours_since_last_funding=4.0,
        )
        conv_high = FundingArbStrategy._compute_conviction(
            z_score=3.0, annualized_pct=45.0, hours_since_last_funding=4.0,
        )
        assert conv_high > conv_low

    def test_conviction_scales_with_proximity(self) -> None:
        """Closer to settlement should produce higher conviction."""
        conv_far = FundingArbStrategy._compute_conviction(
            z_score=3.0, annualized_pct=20.0, hours_since_last_funding=1.0,
        )
        conv_near = FundingArbStrategy._compute_conviction(
            z_score=3.0, annualized_pct=20.0, hours_since_last_funding=7.5,
        )
        assert conv_near > conv_far

    def test_conviction_capped_at_1(self) -> None:
        """Conviction never exceeds 1.0."""
        conv = FundingArbStrategy._compute_conviction(
            z_score=100.0, annualized_pct=500.0, hours_since_last_funding=8.0,
        )
        assert conv <= 1.0

    def test_conviction_in_valid_range(self) -> None:
        """Conviction is always in [0, 1]."""
        for z in [2.0, 3.0, 5.0, 10.0]:
            for rate in [10.0, 20.0, 50.0]:
                for hours in [0.0, 4.0, 8.0]:
                    conv = FundingArbStrategy._compute_conviction(z, rate, hours)
                    assert 0.0 <= conv <= 1.0, f"z={z}, rate={rate}, hours={hours}"


class TestPortfolioRouting:
    """Portfolio A vs B routing tests."""

    def test_high_conviction_routes_to_a(self) -> None:
        """High conviction signals should target Portfolio A."""
        params = FundingArbParams(
            zscore_threshold=1.5,
            min_annualized_rate_pct=1.0,
            min_conviction=0.0,
            min_funding_samples=5,
            cooldown_bars=0,
            portfolio_a_min_conviction=0.5,
        )
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        strategy = FundingArbStrategy(params=params)
        # Very extreme funding → high conviction
        snap = _make_snapshot(funding_rate=0.01, hours_since_last_funding=7.5)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].suggested_target == PortfolioTarget.A

    def test_low_conviction_routes_to_b(self) -> None:
        """Lower conviction signals should target Portfolio B."""
        params = FundingArbParams(
            zscore_threshold=1.5,
            min_annualized_rate_pct=1.0,
            min_conviction=0.0,
            min_funding_samples=5,
            cooldown_bars=0,
            portfolio_a_min_conviction=0.99,  # Very high bar for A
        )
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        strategy = FundingArbStrategy(params=params)
        # Moderate extreme — conviction won't hit 0.99
        snap = _make_snapshot(funding_rate=0.003, hours_since_last_funding=4.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        if signals:
            assert signals[0].suggested_target == PortfolioTarget.B


class TestConfigLoading:
    """YAML config integration tests."""

    def test_from_config_dict(self) -> None:
        """Strategy loads parameters from a config dict."""
        config = {
            "strategy": {"name": "funding_arb", "enabled": True},
            "parameters": {
                "zscore_threshold": 3.0,
                "min_annualized_rate_pct": 15.0,
                "cooldown_bars": 10,
                "portfolio_a_min_conviction": 0.80,
            },
        }
        strategy = FundingArbStrategy(config=config)
        assert strategy.name == "funding_arb"
        assert strategy.enabled is True
        assert strategy._params.zscore_threshold == 3.0
        assert strategy._params.min_annualized_rate_pct == 15.0
        assert strategy._params.cooldown_bars == 10
        assert strategy._params.portfolio_a_min_conviction == 0.80

    def test_disabled_via_config(self) -> None:
        """Strategy can be disabled via config."""
        config = {
            "strategy": {"name": "funding_arb", "enabled": True},
            "parameters": {"enabled": False},
        }
        strategy = FundingArbStrategy(config=config)
        assert strategy.enabled is False

    def test_default_params_without_config(self) -> None:
        """Strategy uses defaults when no config provided."""
        strategy = FundingArbStrategy()
        assert strategy._params.zscore_threshold == 2.0
        assert strategy._params.min_annualized_rate_pct == 10.0
        assert strategy._params.lookback_hours == 168

    def test_properties(self) -> None:
        """Name, enabled, and min_history are correct."""
        strategy = FundingArbStrategy()
        assert strategy.name == "funding_arb"
        assert strategy.enabled is True
        assert strategy.min_history == 19  # atr_period(14) + 5
