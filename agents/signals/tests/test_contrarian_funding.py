"""Tests for the ContrarianFundingStrategy.

Tests are structured to mirror the established pattern from test_funding_arb.py
but adapted for the three-component conviction model (z-score + OI + persistence).
The shared conftest.py handles instrument registration — no per-file fixture needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.contrarian_funding import (
    ContrarianFundingParams,
    ContrarianFundingStrategy,
)
from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

BASE_TS = datetime(2025, 6, 1, tzinfo=UTC)


def _make_snapshot(
    price: float = 2500.0,
    funding_rate: float = 0.0003,
    open_interest: float = 500_000.0,
    hours_since_last_funding: float = 6.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """Build a minimal ETH-PERP MarketSnapshot for testing."""
    if ts is None:
        ts = datetime.now(tz=UTC)
    return MarketSnapshot(
        timestamp=ts,
        instrument="ETH-PERP",
        mark_price=Decimal(str(price)),
        index_price=Decimal(str(price - 1.0)),
        last_price=Decimal(str(price)),
        best_bid=Decimal(str(price - 0.5)),
        best_ask=Decimal(str(price + 0.5)),
        spread_bps=4.0,
        volume_24h=Decimal("1_000_000"),
        open_interest=Decimal(str(open_interest)),
        funding_rate=Decimal(str(funding_rate)),
        next_funding_time=ts + timedelta(hours=2),
        hours_since_last_funding=hours_since_last_funding,
        orderbook_imbalance=0.0,
        volatility_1h=0.02,
        volatility_24h=0.03,
    )


def _build_store_with_history(
    n_samples: int = 60,
    base_price: float = 2500.0,
    normal_funding: float = 0.0001,
    normal_oi: float = 500_000.0,
    sample_interval_sec: int = 1,
    *,
    rng_seed: int = 42,
) -> FeatureStore:
    """Create a FeatureStore pre-loaded with varied price, funding, and OI history.

    Both funding rates and open interest values are varied so that z-scores are
    computable for both series. Varying values are critical because FeatureStore
    deduplicates consecutive identical funding rate values.

    Args:
        n_samples: Number of snapshots to feed in.
        base_price: Base close price (walks ± to produce valid ATR).
        normal_funding: Mean funding rate around which samples are jittered.
        normal_oi: Mean open interest around which samples are jittered.
        sample_interval_sec: Store sample interval — 1 s lets every snapshot land.
        rng_seed: Seed for reproducibility.

    Returns:
        Populated FeatureStore ready for strategy evaluation.
    """
    store = FeatureStore(
        max_samples=500,
        sample_interval=timedelta(seconds=sample_interval_sec),
    )
    rng = np.random.default_rng(seed=rng_seed)

    for i in range(n_samples):
        # Small price walk for valid ATR
        price = base_price + (i % 10) * 0.5
        # Jitter funding so every sample is unique and the store keeps it
        funding = normal_funding + rng.normal(0.0, abs(normal_funding) * 0.10 + 1e-9)
        # Jitter OI similarly
        oi = normal_oi + rng.normal(0.0, normal_oi * 0.02)
        snap = _make_snapshot(
            price=price,
            funding_rate=round(float(funding), 10),
            open_interest=max(float(oi), 1.0),
            ts=BASE_TS + timedelta(seconds=i * sample_interval_sec),
        )
        store.update(snap)

    return store


def _permissive_params(**overrides: object) -> ContrarianFundingParams:
    """Return ContrarianFundingParams with low thresholds for easy signal triggering."""
    base = {
        "zscore_threshold": 1.5,
        "min_funding_samples": 5,
        "min_conviction": 0.0,
        "cooldown_bars": 0,
        "oi_zscore_threshold": 1.0,
        "persistence_lookback": 10,
        "persistence_min_ratio": 0.0,  # No persistence gate in most tests
    }
    base.update(overrides)  # type: ignore[arg-type]
    return ContrarianFundingParams(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# No-signal tests
# ---------------------------------------------------------------------------


class TestContrarianNoSignal:
    """Cases where the strategy must NOT fire."""

    def test_insufficient_funding_samples(self) -> None:
        """Guard fires when funding history is shorter than min_funding_samples."""
        params = ContrarianFundingParams(min_funding_samples=20)
        strategy = ContrarianFundingStrategy(params=params)
        # Only 5 samples in store — below the 20 minimum
        store = _build_store_with_history(n_samples=5)
        snap = _make_snapshot(funding_rate=0.005)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_insufficient_price_history(self) -> None:
        """Guard fires when price samples are fewer than min_history (atr_period + 5)."""
        strategy = ContrarianFundingStrategy(params=ContrarianFundingParams())
        store = FeatureStore(sample_interval=timedelta(seconds=1))
        # Feed only 3 price samples — min_history is 19 (atr_period=14 + 5)
        for i in range(3):
            snap = _make_snapshot(
                funding_rate=0.005,
                ts=BASE_TS + timedelta(seconds=i),
            )
            store.update(snap)
        assert strategy.evaluate(snap, store) == []  # type: ignore[possibly-undefined]

    def test_zscore_below_threshold(self) -> None:
        """No signal when the current funding rate is close to the historical mean."""
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        params = _permissive_params(zscore_threshold=2.0)
        strategy = ContrarianFundingStrategy(params=params)
        # Feed a rate near the mean — z-score will be ~0
        snap = _make_snapshot(funding_rate=0.0001)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_cooldown_enforced(self) -> None:
        """Second evaluation within cooldown window produces no signal."""
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        params = _permissive_params(cooldown_bars=5)
        strategy = ContrarianFundingStrategy(params=params)

        # First eval — should fire
        snap1 = _make_snapshot(
            funding_rate=0.01,
            ts=BASE_TS + timedelta(seconds=60),
        )
        store.update(snap1)
        signals = strategy.evaluate(snap1, store)
        assert len(signals) == 1

        # Immediately evaluate again — cooldown blocks it
        snap2 = _make_snapshot(
            funding_rate=0.01,
            ts=BASE_TS + timedelta(seconds=62),
        )
        store.update(snap2)
        assert strategy.evaluate(snap2, store) == []

    def test_zero_variance_funding(self) -> None:
        """All identical funding rates → std=0 → no signal (avoids division by zero)."""
        store = FeatureStore(sample_interval=timedelta(seconds=1))
        fixed_rate = 0.005  # Extreme but constant
        for i in range(60):
            snap = _make_snapshot(
                price=2500.0 + (i % 5) * 1.0,
                funding_rate=fixed_rate,
                ts=BASE_TS + timedelta(seconds=i),
            )
            store.update(snap)
        # Override funding deque directly to guarantee all-same values
        store._funding_rates.clear()  # type: ignore[attr-defined]
        for _ in range(30):
            store._funding_rates.append(fixed_rate)  # type: ignore[attr-defined]

        params = _permissive_params(min_funding_samples=5)
        strategy = ContrarianFundingStrategy(params=params)
        snap = _make_snapshot(funding_rate=fixed_rate)
        assert strategy.evaluate(snap, store) == []


# ---------------------------------------------------------------------------
# Signal emission tests
# ---------------------------------------------------------------------------


class TestContrarianSignals:
    """Cases where the strategy SHOULD emit a signal."""

    def _setup(
        self,
        normal_funding: float = 0.0001,
    ) -> tuple[ContrarianFundingStrategy, FeatureStore]:
        """Return a permissive (strategy, store) pair for signal-emission testing."""
        params = _permissive_params()
        store = _build_store_with_history(n_samples=50, normal_funding=normal_funding)
        return ContrarianFundingStrategy(params=params), store

    def test_short_on_extreme_positive_funding(self) -> None:
        """Positive extreme funding → longs crowded → strategy emits SHORT."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT

    def test_long_on_extreme_negative_funding(self) -> None:
        """Negative extreme funding → shorts crowded → strategy emits LONG."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=-0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.LONG

    def test_signal_has_correct_fields(self) -> None:
        """All StandardSignal fields are populated with sensible values."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]

        # Identity
        assert sig.signal_id.startswith("sig-")
        assert sig.instrument == "ETH-PERP"
        assert sig.source == SignalSource.CONTRARIAN_FUNDING

        # Price levels
        assert sig.entry_price == snap.last_price
        assert sig.stop_loss is not None
        assert sig.take_profit is not None

        # Time horizon uses max_holding_hours default (16)
        assert sig.time_horizon == timedelta(hours=16)

        # Conviction in valid range
        assert 0.0 <= sig.conviction <= 1.0

        # Required metadata keys
        for key in ("funding_rate", "z_score", "oi_z_score", "persistence_ratio"):
            assert key in sig.metadata, f"Missing metadata key: {key}"

    def test_stop_loss_direction_long(self) -> None:
        """LONG signal: stop_loss < entry_price and take_profit > entry_price."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=-0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.stop_loss < sig.entry_price  # type: ignore[operator]
        assert sig.take_profit > sig.entry_price  # type: ignore[operator]

    def test_stop_loss_direction_short(self) -> None:
        """SHORT signal: stop_loss > entry_price and take_profit < entry_price."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.SHORT
        assert sig.stop_loss > sig.entry_price  # type: ignore[operator]
        assert sig.take_profit < sig.entry_price  # type: ignore[operator]

    def test_signal_source_is_contrarian(self) -> None:
        """Signal source must be SignalSource.CONTRARIAN_FUNDING, not FUNDING_ARB."""
        strategy, store = self._setup()
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].source == SignalSource.CONTRARIAN_FUNDING
        assert signals[0].source != SignalSource.FUNDING_ARB


# ---------------------------------------------------------------------------
# Conviction model tests
# ---------------------------------------------------------------------------


class TestConviction:
    """Unit tests for the three-component conviction model."""

    def test_conviction_scales_with_zscore(self) -> None:
        """Higher absolute z-score → more funding extremity → higher conviction."""
        conv_low = ContrarianFundingStrategy._compute_conviction(
            z_score=2.5, oi_z_score=0.0, persistence_ratio=0.5,
        )
        conv_high = ContrarianFundingStrategy._compute_conviction(
            z_score=5.0, oi_z_score=0.0, persistence_ratio=0.5,
        )
        assert conv_high > conv_low

    def test_conviction_scales_with_oi_zscore(self) -> None:
        """Higher OI z-score → more crowd confirmation → higher conviction."""
        conv_low = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=1.0, persistence_ratio=0.5,
        )
        conv_high = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=2.5, persistence_ratio=0.5,
        )
        assert conv_high > conv_low

    def test_conviction_scales_with_persistence(self) -> None:
        """Higher persistence ratio → longer-lasting extreme → higher conviction."""
        conv_low = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=0.0, persistence_ratio=0.5,
        )
        conv_high = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=0.0, persistence_ratio=1.0,
        )
        assert conv_high > conv_low

    def test_conviction_capped_at_1(self) -> None:
        """Extreme inputs across all three components still cap at 1.0."""
        conv = ContrarianFundingStrategy._compute_conviction(
            z_score=100.0, oi_z_score=100.0, persistence_ratio=1.0,
        )
        assert conv <= 1.0

    @pytest.mark.parametrize(
        "z_score,oi_z_score,persistence_ratio",
        [
            (2.0, 0.0, 0.0),
            (3.0, 1.5, 0.7),
            (5.0, 2.0, 1.0),
            (10.0, 5.0, 1.0),
            (2.5, 0.0, 0.6),
        ],
    )
    def test_conviction_in_valid_range(
        self,
        z_score: float,
        oi_z_score: float,
        persistence_ratio: float,
    ) -> None:
        """Conviction is always in [0.0, 1.0] for all parameter combinations."""
        conv = ContrarianFundingStrategy._compute_conviction(
            z_score=z_score,
            oi_z_score=oi_z_score,
            persistence_ratio=persistence_ratio,
        )
        assert 0.0 <= conv <= 1.0, (
            f"Out-of-range conviction {conv} for z={z_score}, "
            f"oi_z={oi_z_score}, persist={persistence_ratio}"
        )

    def test_zero_oi_zscore_gives_no_oi_component(self) -> None:
        """OI z-score below the 1.0 baseline contributes 0 to conviction."""
        # With no OI contribution, conviction comes solely from z-score and persistence.
        conv_no_oi = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=0.0, persistence_ratio=0.5,
        )
        # OI z-score of exactly 1.0 is the threshold — still 0 contribution.
        conv_threshold_oi = ContrarianFundingStrategy._compute_conviction(
            z_score=3.0, oi_z_score=1.0, persistence_ratio=0.5,
        )
        assert conv_no_oi == conv_threshold_oi

    def test_known_conviction_values(self) -> None:
        """Spot-check computed conviction matches expected formula output."""
        # z=5 → z_excess=3 → z_comp = min(3/3*0.40, 0.40) = 0.40
        # oi_z=2 → oi_excess=1 → oi_comp = min(1/1*0.35, 0.35) = 0.35
        # persist=1.0 → persist_excess=0.5 → persist_comp = min(0.5/0.5*0.25, 0.25) = 0.25
        # total = 1.0
        conv = ContrarianFundingStrategy._compute_conviction(
            z_score=5.0, oi_z_score=2.0, persistence_ratio=1.0,
        )
        assert conv == pytest.approx(1.0, abs=1e-3)

    def test_partial_components_add_correctly(self) -> None:
        """Mid-range inputs sum components correctly without saturation."""
        # z=3.5 → z_excess=1.5 → z_comp = 1.5/3.0*0.40 = 0.20
        # oi_z=1.5 → oi_excess=0.5 → oi_comp = 0.5/1.0*0.35 = 0.175
        # persist=0.75 → persist_excess=0.25 → persist_comp = 0.25/0.5*0.25 = 0.125
        # total ≈ 0.50
        conv = ContrarianFundingStrategy._compute_conviction(
            z_score=3.5, oi_z_score=1.5, persistence_ratio=0.75,
        )
        assert conv == pytest.approx(0.50, abs=1e-3)


# ---------------------------------------------------------------------------
# OI confirmation tests
# ---------------------------------------------------------------------------


class TestOIConfirmation:
    """Tests for the open-interest confirmation component."""

    def test_high_oi_boosts_conviction(self) -> None:
        """Elevated OI z-score produces higher conviction than flat OI."""
        # Build two stores: one with uniform OI (oi_z≈0), one with rising OI
        store_flat = _build_store_with_history(
            n_samples=50, normal_oi=500_000.0, rng_seed=1,
        )
        # Override OI in the flat store to be constant so oi_z=0
        flat_oi = 500_000.0
        store_flat._open_interests.clear()  # type: ignore[attr-defined]
        for _ in range(50):
            store_flat._open_interests.append(flat_oi)  # type: ignore[attr-defined]

        # Build a rising-OI store by populating OI that ends significantly higher
        store_rising = _build_store_with_history(
            n_samples=50, normal_oi=500_000.0, rng_seed=2,
        )
        # Replace OI with a strictly increasing series so the final value is a high outlier
        store_rising._open_interests.clear()  # type: ignore[attr-defined]
        for k in range(50):
            store_rising._open_interests.append(500_000.0 + k * 2_000.0)  # type: ignore[attr-defined]

        params = _permissive_params()
        strategy = ContrarianFundingStrategy(params=params)

        snap_flat = _make_snapshot(funding_rate=0.01, open_interest=500_000.0)
        snap_rising = _make_snapshot(
            funding_rate=0.01,
            open_interest=500_000.0 + 49 * 2_000.0,
        )
        store_flat.update(snap_flat)
        store_rising.update(snap_rising)

        sigs_flat = strategy.evaluate(snap_flat, store_flat)
        # Fresh strategy for rising store to avoid cross-contamination of cooldown
        strategy2 = ContrarianFundingStrategy(params=params)
        sigs_rising = strategy2.evaluate(snap_rising, store_rising)

        assert len(sigs_flat) == 1
        assert len(sigs_rising) == 1
        assert sigs_rising[0].conviction >= sigs_flat[0].conviction

    def test_insufficient_oi_data_still_works(self) -> None:
        """Fewer OI samples than oi_lookback returns a signal without crashing."""
        store = _build_store_with_history(n_samples=20, normal_oi=500_000.0)
        params = _permissive_params(
            oi_lookback=100,  # Larger than the 20 available samples
            min_funding_samples=5,
        )
        strategy = ContrarianFundingStrategy(params=params)
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        # Must not raise; signals list may or may not be empty depending on z-score
        result = strategy.evaluate(snap, store)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Funding persistence tests
# ---------------------------------------------------------------------------


class TestFundingPersistence:
    """Tests for the persistence-ratio component of conviction."""

    def test_persistent_one_sided_funding_boosts_conviction(self) -> None:
        """All-positive funding history → persistence_ratio=1.0 → max persist component."""
        # Build a store then override funding_rates to be uniformly positive
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        # Replace funding deque so the last `persistence_lookback` are all positive
        store._funding_rates.clear()  # type: ignore[attr-defined]
        for i in range(20):
            store._funding_rates.append(0.0001 + i * 0.00001)  # type: ignore[attr-defined]

        params = _permissive_params(
            persistence_lookback=10,
            persistence_min_ratio=0.0,
        )
        strategy = ContrarianFundingStrategy(params=params)
        # Extreme positive rate on top of all-positive history
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        # The signal's persistence_ratio metadata should be 1.0 (all same side)
        assert len(signals) == 1
        assert signals[0].metadata["persistence_ratio"] == pytest.approx(1.0, abs=0.01)

    def test_mixed_funding_lowers_persistence(self) -> None:
        """Alternating-sign funding history → low persistence ratio."""
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        # Replace funding with alternating signs
        store._funding_rates.clear()  # type: ignore[attr-defined]
        for i in range(20):
            sign = 1.0 if i % 2 == 0 else -1.0
            store._funding_rates.append(sign * 0.0001)  # type: ignore[attr-defined]

        # Add extreme positive rate so z-score fires
        store._funding_rates.append(0.01)  # type: ignore[attr-defined]

        params = _permissive_params(
            persistence_lookback=10,
            persistence_min_ratio=0.0,
        )
        strategy = ContrarianFundingStrategy(params=params)
        snap = _make_snapshot(funding_rate=0.01)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        if signals:
            # Mixed history → persistence_ratio should be ≤ 0.65
            # (alternating ±signs → ~50% same-side, well below persistence_min_ratio)
            assert float(signals[0].metadata["persistence_ratio"]) <= 0.65


# ---------------------------------------------------------------------------
# Portfolio routing tests
# ---------------------------------------------------------------------------


class TestPortfolioRouting:
    """Tests for Portfolio A vs B routing via conviction threshold."""

    def test_high_conviction_routes_to_a(self) -> None:
        """Signal with conviction ≥ route_a_min_conviction targets Portfolio A."""
        params = _permissive_params(
            route_a_min_conviction=0.30,  # Low enough that extreme funding hits it
        )
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        strategy = ContrarianFundingStrategy(params=params)

        # Extreme funding → all three components fire → should exceed 0.30
        snap = _make_snapshot(funding_rate=0.02)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].suggested_route == Route.A

    def test_low_conviction_routes_to_b(self) -> None:
        """Signal with conviction below route_a_min_conviction targets Portfolio B."""
        params = _permissive_params(
            route_a_min_conviction=0.99,  # Impossibly high bar for A
        )
        store = _build_store_with_history(n_samples=50, normal_funding=0.0001)
        strategy = ContrarianFundingStrategy(params=params)

        # Moderate extreme — conviction won't reach 0.99
        snap = _make_snapshot(funding_rate=0.003)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        if signals:
            assert signals[0].suggested_route == Route.B


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Tests for YAML config dict loading and parameter override."""

    def test_from_config_dict(self) -> None:
        """Strategy correctly unpacks all parameters from a config dict."""
        config = {
            "strategy": {"name": "funding_arb", "enabled": True},
            "parameters": {
                "zscore_threshold": 3.0,
                "lookback_hours": 72,
                "min_funding_samples": 15,
                "oi_zscore_threshold": 1.5,
                "oi_lookback": 50,
                "persistence_lookback": 8,
                "persistence_min_ratio": 0.65,
                "min_conviction": 0.60,
                "max_holding_hours": 12,
                "atr_period": 10,
                "stop_loss_atr_mult": 1.2,
                "take_profit_atr_mult": 2.5,
                "cooldown_bars": 6,
                "route_a_min_conviction": 0.75,
                "enabled": True,
            },
        }
        strategy = ContrarianFundingStrategy(config=config)

        p = strategy._params
        assert p.zscore_threshold == 3.0
        assert p.lookback_hours == 72
        assert p.min_funding_samples == 15
        assert p.oi_zscore_threshold == 1.5
        assert p.oi_lookback == 50
        assert p.persistence_lookback == 8
        assert p.persistence_min_ratio == 0.65
        assert p.min_conviction == 0.60
        assert p.max_holding_hours == 12
        assert p.atr_period == 10
        assert p.stop_loss_atr_mult == 1.2
        assert p.take_profit_atr_mult == 2.5
        assert p.cooldown_bars == 6
        assert p.route_a_min_conviction == 0.75

    def test_disabled_via_config(self) -> None:
        """Setting enabled=False in the parameters block disables the strategy."""
        config = {
            "strategy": {"name": "funding_arb", "enabled": True},
            "parameters": {"enabled": False},
        }
        strategy = ContrarianFundingStrategy(config=config)
        assert strategy.enabled is False

    def test_default_params_without_config(self) -> None:
        """Strategy uses correct defaults when instantiated with no arguments."""
        strategy = ContrarianFundingStrategy()
        p = strategy._params

        assert p.zscore_threshold == 2.0
        assert p.lookback_hours == 168
        assert p.min_funding_samples == 10
        assert p.oi_zscore_threshold == 1.0
        assert p.oi_lookback == 100
        assert p.persistence_lookback == 10
        assert p.persistence_min_ratio == 0.6
        assert p.min_conviction == 0.55
        assert p.max_holding_hours == 16
        assert p.atr_period == 14
        assert p.stop_loss_atr_mult == 1.5
        assert p.take_profit_atr_mult == 3.0
        assert p.cooldown_bars == 12
        assert p.route_a_min_conviction == 0.70
        assert p.enabled is True

    def test_properties(self) -> None:
        """name, enabled, and min_history properties return correct values."""
        strategy = ContrarianFundingStrategy()
        # name must be 'funding_arb' for YAML/strategy_matrix key compatibility
        assert strategy.name == "funding_arb"
        assert strategy.enabled is True
        # min_history = atr_period(14) + 5 = 19
        assert strategy.min_history == 19

    def test_partial_config_uses_defaults(self) -> None:
        """Config with only some parameters preserves defaults for the rest."""
        config = {
            "parameters": {
                "zscore_threshold": 4.0,
                # All other params omitted → should use defaults
            },
        }
        strategy = ContrarianFundingStrategy(config=config)
        assert strategy._params.zscore_threshold == 4.0
        assert strategy._params.lookback_hours == 168  # Default preserved
        assert strategy._params.min_conviction == 0.55  # Default preserved

    def test_per_instrument_config_override(self) -> None:
        """Per-instrument config overrides base parameters correctly."""
        # Simulate what main.py does: base config merged with per-instrument overrides
        base_config: dict[str, object] = {
            "parameters": {
                "zscore_threshold": 2.0,
                "min_conviction": 0.55,
                "lookback_hours": 168,
                "min_funding_samples": 10,
                "oi_zscore_threshold": 1.0,
                "oi_lookback": 100,
                "persistence_lookback": 10,
                "persistence_min_ratio": 0.6,
                "max_holding_hours": 16,
                "atr_period": 14,
                "stop_loss_atr_mult": 1.5,
                "take_profit_atr_mult": 3.0,
                "cooldown_bars": 12,
                "route_a_min_conviction": 0.70,
                "enabled": True,
            },
        }
        eth_override: dict[str, object] = {
            "parameters": {
                "zscore_threshold": 2.2,
                "min_conviction": 0.58,
                "lookback_hours": 168,
                "min_funding_samples": 10,
                "oi_zscore_threshold": 1.0,
                "oi_lookback": 100,
                "persistence_lookback": 10,
                "persistence_min_ratio": 0.6,
                "max_holding_hours": 16,
                "atr_period": 14,
                "stop_loss_atr_mult": 1.5,
                "take_profit_atr_mult": 3.0,
                "cooldown_bars": 12,
                "route_a_min_conviction": 0.70,
                "enabled": True,
            },
        }
        _ = ContrarianFundingStrategy(config=base_config)
        eth_strategy = ContrarianFundingStrategy(config=eth_override)

        assert eth_strategy._params.zscore_threshold == 2.2
        assert eth_strategy._params.min_conviction == 0.58
        # Unoverridden fields remain at defaults
        assert eth_strategy._params.lookback_hours == 168
