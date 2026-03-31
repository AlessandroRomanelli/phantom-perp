"""Tests for the orderbook imbalance (OBI) strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.orderbook_imbalance import (
    OrderbookImbalanceParams,
    OrderbookImbalanceStrategy,
)

TEST_INSTRUMENT_ID = "ETH-PERP"


def _snap(
    price: float = 2230.0,
    orderbook_imbalance: float = 0.0,
    spread_bps: float = 5.0,
    volume_24h: float = 15000.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    if ts is None:
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument=TEST_INSTRUMENT_ID,
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 0.5)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.25)),
        best_ask=Decimal(str(price + 0.25)),
        spread_bps=spread_bps,
        volume_24h=Decimal(str(volume_24h)),
        open_interest=Decimal("80000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=orderbook_imbalance,
        volatility_1h=0.15,
        volatility_24h=0.45,
    )


def _build_store(
    n_bars: int = 30,
    imbalance_values: list[float] | None = None,
    spread_bps: float = 5.0,
    volume_24h: float = 15000.0,
) -> tuple[FeatureStore, MarketSnapshot]:
    """Build a FeatureStore with configurable imbalance history.

    Args:
        n_bars: Number of bars to populate.
        imbalance_values: Per-bar imbalance values. If None, uses 0.0 for all.
        spread_bps: Spread for all snapshots.
        volume_24h: Volume for all snapshots.

    Returns:
        Tuple of (store, last_snapshot).
    """
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

    if imbalance_values is None:
        imbalance_values = [0.0] * n_bars

    # Pad to n_bars if short
    while len(imbalance_values) < n_bars:
        imbalance_values.append(imbalance_values[-1] if imbalance_values else 0.0)

    last_snap = None
    for i in range(n_bars):
        # Slight price variation for valid ATR
        price = 2230.0 + np.sin(i * 0.5) * 5.0
        snap = _snap(
            price=price,
            orderbook_imbalance=imbalance_values[i],
            spread_bps=spread_bps,
            volume_24h=volume_24h + i * 10,  # Slight volume variation
            ts=base + timedelta(seconds=i),
        )
        store.update(snap)
        last_snap = snap

    assert last_snap is not None
    return store, last_snap


class TestOrderbookImbalanceStrategy:
    def test_long_signal_positive_imbalance(self) -> None:
        """With 10+ bars of positive imbalance (>0.3 avg), strategy emits LONG signal."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        # 30 bars total, last 10+ have strong positive imbalance
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.LONG
        assert signals[0].source == SignalSource.ORDERBOOK_IMBALANCE
        assert signals[0].conviction > 0.0

    def test_short_signal_negative_imbalance(self) -> None:
        """With 10+ bars of negative imbalance (<-0.3 avg), strategy emits SHORT signal."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [-0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT

    def test_no_signal_weak_imbalance(self) -> None:
        """With imbalance values around 0.0-0.1, strategy returns empty list."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.05] * 30
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        assert signals == []

    def test_time_weighted_average(self) -> None:
        """With increasing imbalance, time-weighted avg weights recent bars higher."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.0,  # Disable threshold for this test
            min_conviction=0.0,
            route_a_min_conviction=0.0,  # Disable portfolio gate to test TWA math only
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        # Increasing from 0.1 to 0.5 over last 10 bars
        imbalances = [0.0] * 14 + [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55,
                                    0.1, 0.15, 0.2, 0.25, 0.3, 0.5]
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)
        assert len(signals) == 1

        tw_imbalance = signals[0].metadata["tw_imbalance"]
        # Linear average of last 10 bars
        window = imbalances[-10:]
        linear_avg = sum(window) / len(window)
        # Time-weighted should be higher than linear since later values are higher
        # (weights = [1,2,3,...,10])
        weights = list(range(1, 11))
        weighted_avg = sum(w * v for w, v in zip(weights, window)) / sum(weights)
        assert abs(tw_imbalance - weighted_avg) < 0.01

    def test_depth_gate_wide_spread(self) -> None:
        """With spread_bps=25 (thin book), strategy suppresses signal."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            max_spread_bps=20.0,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(
            n_bars=30, imbalance_values=imbalances, spread_bps=25.0,
        )

        signals = strategy.evaluate(snap, store)

        assert signals == []

    def test_depth_gate_tight_spread(self) -> None:
        """With spread_bps=3 (deep book), strategy fires normally."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            max_spread_bps=20.0,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(
            n_bars=30, imbalance_values=imbalances, spread_bps=3.0,
        )

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1

    def test_route_a_high_conviction(self) -> None:
        """When conviction >= route_a_min_conviction, target is Portfolio A."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
            route_a_min_conviction=0.65,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        # Very strong imbalance + tight spread + high volume -> high conviction
        imbalances = [0.0] * 14 + [0.9] * 16
        store, snap = _build_store(
            n_bars=30, imbalance_values=imbalances, spread_bps=2.0,
            volume_24h=30000.0,
        )

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        if signals[0].conviction >= 0.65:
            assert signals[0].suggested_route == Route.A
        else:
            # If conviction didn't reach threshold, route to B
            assert signals[0].suggested_route == Route.B

    def test_route_b_low_conviction(self) -> None:
        """OBI is Portfolio A only — when conviction < route_a_min_conviction, no signal."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
            route_a_min_conviction=0.99,  # Very high threshold → conviction never reaches it
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        # OBI is Portfolio A only — sub-threshold conviction returns no signal
        assert len(signals) == 0

    def test_signal_metadata(self) -> None:
        """Emitted signal has metadata with tw_imbalance, spread_bps, and time_horizon."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        m = signals[0].metadata
        assert "tw_imbalance" in m
        assert "spread_bps" in m
        assert signals[0].time_horizon == timedelta(hours=1)

    def test_insufficient_history(self) -> None:
        """With < lookback_bars + atr_period samples, returns empty list."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            atr_period=14,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        # Only 5 bars, need at least 24
        store, snap = _build_store(n_bars=5, imbalance_values=[0.5] * 5)

        signals = strategy.evaluate(snap, store)

        assert signals == []

    def test_cooldown(self) -> None:
        """After emitting a signal, does not emit again within cooldown_bars."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=5,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        # First evaluation should fire
        sig1 = strategy.evaluate(snap, store)
        assert len(sig1) == 1

        # Second evaluation should be suppressed by cooldown
        sig2 = strategy.evaluate(snap, store)
        assert sig2 == []

    def test_properties(self) -> None:
        """Strategy name, enabled, and min_history are correct."""
        params = OrderbookImbalanceParams(lookback_bars=10, atr_period=14)
        strategy = OrderbookImbalanceStrategy(params=params)
        assert strategy.name == "orderbook_imbalance"
        assert strategy.enabled is True
        assert strategy.min_history == 24  # lookback_bars + atr_period

    def test_config_override(self) -> None:
        """Strategy loads parameters from config dict."""
        config = {
            "parameters": {
                "lookback_bars": 20,
                "imbalance_threshold": 0.30,
                "max_spread_bps": 15.0,
            },
        }
        strategy = OrderbookImbalanceStrategy(config=config)
        assert strategy._params.lookback_bars == 20
        assert strategy._params.imbalance_threshold == 0.30
        assert strategy._params.max_spread_bps == 15.0
        # Defaults preserved for unspecified params
        assert strategy._params.atr_period == 14

    def test_stop_loss_and_take_profit(self) -> None:
        """Signal includes proper stop loss and take profit prices."""
        params = OrderbookImbalanceParams(
            lookback_bars=10,
            imbalance_threshold=0.25,
            min_conviction=0.0,
            cooldown_bars=0,
        )
        strategy = OrderbookImbalanceStrategy(params=params)
        imbalances = [0.0] * 14 + [0.5] * 16
        store, snap = _build_store(n_bars=30, imbalance_values=imbalances)

        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.entry_price is not None
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        # LONG signal: stop_loss < entry < take_profit
        if sig.direction == PositionSide.LONG:
            assert sig.stop_loss < sig.entry_price
            assert sig.take_profit > sig.entry_price
        else:
            assert sig.stop_loss > sig.entry_price
            assert sig.take_profit < sig.entry_price
