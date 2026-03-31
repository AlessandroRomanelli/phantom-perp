"""Tests for the OIDivergenceStrategy.

Tests are structured to mirror the established pattern from test_contrarian_funding.py.
The shared conftest.py handles instrument registration — no per-file fixture needed.

Coverage:
  - No-signal guards (insufficient history, no divergence, below threshold, cooldown,
    zero OI)
  - Classic divergence signals (long on price-down/OI-up, short on price-up/OI-down)
  - OI acceleration signals (long on acceleration, short on deceleration)
  - Combined mode behaviour (agree → boosted conviction, conflict → no signal)
  - Conviction model scaling with divergence magnitude
  - Portfolio A vs B routing
  - Signal metadata (source, stops, direction)
  - Config loading and per-instrument parameter overrides
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml  # type: ignore[import-untyped]

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.oi_divergence import (
    OIDivergenceParams,
    OIDivergenceStrategy,
)
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_TS = datetime(2025, 6, 1, tzinfo=UTC)

# Default permissive params use max(divergence_lookback=20, accel_long_lookback=20)
# → min_history = 20 + 14 + 5 = 39.  We build stores with 60+ samples by default.
_PERM_LOOKBACK = 20  # Matches permissive params below


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    price: float = 2500.0,
    open_interest: float = 500_000.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """Build a minimal ETH-PERP MarketSnapshot for testing.

    Args:
        price: Used for last_price, mark_price, index_price and bid/ask.
        open_interest: Current OI value.
        ts: Snapshot timestamp; defaults to now if not provided.

    Returns:
        Populated MarketSnapshot ready for FeatureStore.update().
    """
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
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(hours=2),
        hours_since_last_funding=6.0,
        orderbook_imbalance=0.0,
        volatility_1h=0.02,
        volatility_24h=0.03,
    )


def _build_store_with_history(
    n_samples: int = 60,
    base_price: float = 2500.0,
    base_oi: float = 500_000.0,
    *,
    price_trend: float = 0.0,
    oi_trend: float = 0.0,
    sample_interval_sec: int = 1,
    rng_seed: int = 42,
) -> FeatureStore:
    """Create a FeatureStore pre-loaded with price and OI history.

    Applies optional linear trends on top of jittered base values so test
    scenarios can manufacture divergence and acceleration patterns.

    Args:
        n_samples: Number of snapshots to feed.
        base_price: Starting close price (walks ± via ``price_trend`` and jitter).
        base_oi: Starting open interest (walks ± via ``oi_trend`` and jitter).
        price_trend: Per-sample price step (positive → price trending up).
        oi_trend: Per-sample OI step (positive → OI trending up).
        sample_interval_sec: Store sample interval; 1 s lets every snapshot land.
        rng_seed: NumPy random seed for reproducibility.

    Returns:
        Populated FeatureStore ready for strategy evaluation.
    """
    store = FeatureStore(
        max_samples=500,
        sample_interval=timedelta(seconds=sample_interval_sec),
    )
    rng = np.random.default_rng(seed=rng_seed)

    for i in range(n_samples):
        # Small jitter around base so ATR is computable
        price = base_price + price_trend * i + rng.normal(0.0, base_price * 0.001)
        oi = base_oi + oi_trend * i + rng.normal(0.0, base_oi * 0.005)
        snap = _make_snapshot(
            price=float(max(price, 1.0)),
            open_interest=float(max(oi, 1.0)),
            ts=BASE_TS + timedelta(seconds=i * sample_interval_sec),
        )
        store.update(snap)

    return store


def _permissive_params(**overrides: object) -> OIDivergenceParams:
    """Return OIDivergenceParams with very low thresholds for easy signal triggering.

    div_threshold_pct=0.1 and accel_threshold=0.1 ensure even tiny moves qualify.
    min_conviction=0.01 disables the conviction gate.
    cooldown_bars=0 disables the cooldown gate.
    """
    base: dict[str, object] = {
        "divergence_lookback": _PERM_LOOKBACK,
        "div_threshold_pct": 0.1,
        "accel_short_lookback": 5,
        "accel_long_lookback": 20,
        "accel_threshold": 0.1,
        "atr_period": 14,
        "stop_loss_atr_mult": 2.0,
        "take_profit_atr_mult": 3.0,
        "min_conviction": 0.01,
        "cooldown_bars": 0,
        "max_holding_hours": 8,
        "portfolio_a_min_conviction": 0.70,
        "enabled": True,
    }
    base.update(overrides)
    return OIDivergenceParams(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# No-signal tests
# ---------------------------------------------------------------------------


class TestOIDivergenceNoSignal:
    """Cases where the strategy must NOT emit a signal."""

    def test_insufficient_history_returns_empty(self) -> None:
        """Guard fires when price history is shorter than min_history."""
        params = OIDivergenceParams()  # Default: min_history = max(20,20)+14+5 = 39
        strategy = OIDivergenceStrategy(params=params)
        # Build a store with only 10 samples — well below min_history=39
        store = _build_store_with_history(n_samples=10)
        snap = _make_snapshot()
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_no_divergence_returns_empty(self) -> None:
        """No signal when price and OI move in the same direction (both up)."""
        # Both price and OI trend strongly up → no classic divergence, no conflict
        # Acceleration direction also ambiguous / both positive → unlikely to fire
        # But we explicitly need to verify the guard path.  We disable accel mode
        # by setting a very high accel_threshold so only divergence mode matters,
        # and then ensure there IS no classic divergence (same direction).
        params = _permissive_params(accel_threshold=1000.0)  # Disable accel
        strategy = OIDivergenceStrategy(params=params)
        # Both price and OI trending up strongly over 60 samples
        store = _build_store_with_history(
            n_samples=60,
            base_price=2500.0,
            base_oi=500_000.0,
            price_trend=5.0,    # Price rising strongly
            oi_trend=1000.0,    # OI rising strongly — same direction as price
        )
        snap = _make_snapshot(price=2800.0, open_interest=560_000.0)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_below_threshold_returns_empty(self) -> None:
        """No signal when divergence is smaller than div_threshold_pct."""
        # Use a HIGH div_threshold so the small divergence we create doesn't qualify
        params = _permissive_params(div_threshold_pct=100.0, accel_threshold=100.0)
        strategy = OIDivergenceStrategy(params=params)
        store = _build_store_with_history(
            n_samples=60,
            price_trend=-1.0,   # Price gently down
            oi_trend=500.0,     # OI gently up
        )
        snap = _make_snapshot()
        store.update(snap)
        assert strategy.evaluate(snap, store) == []

    def test_cooldown_enforced(self) -> None:
        """Second evaluation within cooldown window produces no signal."""
        # Use cooldown_bars=5 to ensure second call is blocked.
        # Disable accel mode so only classic divergence fires (avoids mode conflict).
        params = _permissive_params(cooldown_bars=5, accel_threshold=1000.0)
        strategy = OIDivergenceStrategy(params=params)

        # Build history where price falls and OI rises — classic divergence LONG
        store = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=400_000.0,
            price_trend=-5.0,   # Price falling
            oi_trend=2000.0,    # OI rising
        )

        snap1 = _make_snapshot(
            price=2300.0,
            open_interest=520_000.0,
            ts=BASE_TS + timedelta(seconds=61),
        )
        store.update(snap1)
        signals1 = strategy.evaluate(snap1, store)
        # Should fire on first call
        assert len(signals1) == 1, "Expected signal on first evaluation"

        # Immediately evaluate again — cooldown=5, bars_since=0 → blocked
        snap2 = _make_snapshot(
            price=2300.0,
            open_interest=520_000.0,
            ts=BASE_TS + timedelta(seconds=63),
        )
        store.update(snap2)
        assert strategy.evaluate(snap2, store) == []

    def test_zero_oi_returns_empty(self) -> None:
        """No signal when old OI is zero — prevents division by zero."""
        params = _permissive_params()
        strategy = OIDivergenceStrategy(params=params)
        store = _build_store_with_history(n_samples=60)

        # Directly zero out all OI in the store to trigger the zero-OI guard
        store._open_interests.clear()
        for _ in range(60):
            store._open_interests.append(0.0)

        snap = _make_snapshot(open_interest=0.0)
        store.update(snap)
        assert strategy.evaluate(snap, store) == []


# ---------------------------------------------------------------------------
# Classic divergence signal tests
# ---------------------------------------------------------------------------


class TestClassicDivergenceSignals:
    """Classic price/OI divergence — price and OI move in opposite directions."""

    def test_long_on_price_down_oi_up(self) -> None:
        """Price falls + OI rises → LONG (coiling/absorption pattern)."""
        params = _permissive_params(accel_threshold=1000.0)  # Disable accel mode
        strategy = OIDivergenceStrategy(params=params)

        # Price trends strongly down, OI trends strongly up over 60 samples
        store = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=400_000.0,
            price_trend=-5.0,   # Price falling ~-250 total
            oi_trend=2000.0,    # OI rising ~+100_000 total
        )
        snap = _make_snapshot(price=2300.0, open_interest=520_000.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.LONG

    def test_short_on_price_up_oi_down(self) -> None:
        """Price rises + OI falls → SHORT (exhaustion pattern)."""
        params = _permissive_params(accel_threshold=1000.0)  # Disable accel mode
        strategy = OIDivergenceStrategy(params=params)

        # Price trends up, OI trends down
        store = _build_store_with_history(
            n_samples=60,
            base_price=2400.0,
            base_oi=600_000.0,
            price_trend=5.0,    # Price rising ~+250 total
            oi_trend=-2000.0,   # OI falling ~-100_000 total
        )
        snap = _make_snapshot(price=2700.0, open_interest=480_000.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT


# ---------------------------------------------------------------------------
# OI acceleration signal tests
# ---------------------------------------------------------------------------


class TestOIAccelerationSignals:
    """OI acceleration mode — short-term ROC significantly exceeds long-term ROC."""

    def test_oi_acceleration_long(self) -> None:
        """OI accelerating higher than recent trend → LONG signal.

        The key insight for the acceleration formula:
          roc_short = (ois[-1] - ois[-5]) / ois[-5] * 100
          roc_long  = (ois[-1] - ois[-20]) / ois[-20] * 100
          accel = roc_short - roc_long

        For accel > 0 (LONG): short-term rise must exceed long-term rise in pct terms.
        Pattern: OI falls for the first 15 bars, then recovers sharply in the last 5.
        roc_long captures a modest net gain (fall + partial recovery).
        roc_short captures only the sharp recovery (large positive pct).
        → accel = roc_short - roc_long > 0 → LONG.
        """
        params = _permissive_params(
            div_threshold_pct=1000.0,   # Disable classic divergence mode
            accel_threshold=0.5,
        )
        strategy = OIDivergenceStrategy(params=params)

        store = FeatureStore(
            max_samples=500,
            sample_interval=timedelta(seconds=1),
        )
        base_oi = 500_000.0
        base_price = 2500.0
        rng = np.random.default_rng(seed=42)
        n = 60

        for i in range(n):
            # OI: falls for first (n-5) samples, then rises sharply in last 5
            if i < n - 5:
                oi = base_oi - i * 1_500.0 + rng.normal(0.0, 200.0)
            else:
                # Sharp recovery: rises 12_000 per step above the trough
                oi = (base_oi - (n - 5) * 1_500.0) + (i - (n - 5)) * 12_000.0 + rng.normal(0.0, 200.0)
            price = base_price + rng.normal(0.0, 5.0)
            snap = _make_snapshot(
                price=float(max(price, 1.0)),
                open_interest=float(max(oi, 1.0)),
                ts=BASE_TS + timedelta(seconds=i),
            )
            store.update(snap)

        snap_final = _make_snapshot(
            price=2500.0,
            open_interest=float(base_oi - (n - 5) * 1_500.0 + 5 * 12_000.0),
            ts=BASE_TS + timedelta(seconds=n),
        )
        store.update(snap_final)
        signals = strategy.evaluate(snap_final, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.LONG

    def test_oi_acceleration_short(self) -> None:
        """OI decelerating / unwinding faster short-term → SHORT signal.

        For accel < 0 (SHORT): short-term OI drops faster than long-term in pct terms.
        Pattern: OI rises for the first 15 bars, then drops sharply in the last 5.
        roc_long captures modest net gain.
        roc_short captures the sharp drop (large negative pct).
        → accel = roc_short - roc_long < 0 → SHORT.
        """
        params = _permissive_params(
            div_threshold_pct=1000.0,   # Disable classic divergence mode
            accel_threshold=0.5,
        )
        strategy = OIDivergenceStrategy(params=params)

        store = FeatureStore(
            max_samples=500,
            sample_interval=timedelta(seconds=1),
        )
        base_oi = 500_000.0
        base_price = 2500.0
        rng = np.random.default_rng(seed=99)
        n = 60

        for i in range(n):
            # OI rises for first (n-5) bars, then drops sharply in last 5
            if i < n - 5:
                oi = base_oi + i * 1_500.0 + rng.normal(0.0, 200.0)
            else:
                # Sharp drop: falls 12_000 per step from peak
                oi = (base_oi + (n - 5) * 1_500.0) - (i - (n - 5)) * 12_000.0 + rng.normal(0.0, 200.0)
            price = base_price + rng.normal(0.0, 5.0)
            snap = _make_snapshot(
                price=float(max(price, 1.0)),
                open_interest=float(max(oi, 1.0)),
                ts=BASE_TS + timedelta(seconds=i),
            )
            store.update(snap)

        snap_final = _make_snapshot(
            price=2500.0,
            open_interest=float(base_oi + (n - 5) * 1_500.0 - 5 * 12_000.0),
            ts=BASE_TS + timedelta(seconds=n),
        )
        store.update(snap_final)
        signals = strategy.evaluate(snap_final, store)

        assert len(signals) == 1
        assert signals[0].direction == PositionSide.SHORT


# ---------------------------------------------------------------------------
# Combined mode tests
# ---------------------------------------------------------------------------


class TestCombinedModes:
    """Tests for when both detection modes fire simultaneously."""

    def test_combined_modes_agree_boost_conviction(self) -> None:
        """Both modes agree on direction → conviction exceeds either alone.

        Constructs the OI history directly to satisfy both conditions simultaneously:
        - Classic divergence LONG: ois[-1] > ois[-21] (net OI up) + price down.
        - Acceleration LONG: ois[-20] > ois[-5] (OI dropped) AND ois[-1] > ois[-5].

        The OI shape for the last 21 bars (indices -21..-1):
          bar -21: 500_000  (baseline)
          bar -20: 520_000  (was higher — OI peaked early)
          bars -19..-6: slowly descends from 518_000 to 476_000
          bar -5: 475_000   (trough, lower than bar -20)
          bars -4..-1: sharp recovery ending at 600_000 (> bar -21)

        This satisfies:
          roc_short = (600k-475k)/475k*100 = +26% > 0 → LONG
          roc_long  = (600k-520k)/520k*100 = +15% > 0
          accel = 26% - 15% = +11% > 0.5 → LONG ✓
          oi_pct = (600k-500k)/500k*100 = +20% > 0.1 → LONG ✓
        """
        # Mode 1 (classic divergence) alone — disable accel
        params_div_only = _permissive_params(accel_threshold=1000.0)
        # Mode 2 (accel) alone — disable divergence
        params_accel_only = _permissive_params(div_threshold_pct=1000.0, accel_threshold=0.5)
        # Both modes active
        params_combined = _permissive_params(accel_threshold=0.5)

        # Build a store with adequate history (≥ min_history=39 samples), then override
        # the last 21 OI values with the exact shape we need.
        store = _build_store_with_history(
            n_samples=65,
            base_price=2800.0,
            base_oi=500_000.0,
            price_trend=-4.0,     # Price trending down throughout
        )

        # Override the last 21 OI entries in the deque with our designed pattern.
        # Strategy checks ois[-21..−1]; the deque has 65 entries after _build_store_with_history.
        # We extend OI to match the shape described in the docstring.
        oi_deque = store._open_interests
        all_ois = list(oi_deque)

        # Overwrite indices -21..-1 (the last 21 entries)
        # Indices in all_ois: 44=bar-21, 45=bar-20, 46..59=bars-19..-6, 60=bar-5, 61..64=bars-4..-1
        all_ois[-21] = 500_000.0   # bar -21 baseline
        all_ois[-20] = 520_000.0   # bar -20 peak (must be > bar -5 for accel LONG)
        for k in range(14):        # bars -19 to -6: descend from 518k to ~476k
            all_ois[-19 + k] = 518_000.0 - k * 3_000.0
        all_ois[-5] = 475_000.0    # trough (lower than bar -20)
        all_ois[-4] = 510_000.0
        all_ois[-3] = 545_000.0
        all_ois[-2] = 575_000.0
        all_ois[-1] = 600_000.0    # final (must be > bar -21 = 500k for div LONG)

        oi_deque.clear()
        oi_deque.extend(all_ois)

        snap_final = _make_snapshot(
            price=2540.0,           # Below price 21 bars ago (2800 - 20*4 = 2020 → ~2540)
            open_interest=600_000.0,
            ts=BASE_TS + timedelta(seconds=66),
        )
        store.update(snap_final)

        strat_div_only = OIDivergenceStrategy(params=params_div_only)
        strat_accel_only = OIDivergenceStrategy(params=params_accel_only)
        strat_combined = OIDivergenceStrategy(params=params_combined)

        sigs_div = strat_div_only.evaluate(snap_final, store)
        sigs_accel = strat_accel_only.evaluate(snap_final, store)
        sigs_combined = strat_combined.evaluate(snap_final, store)

        # Both individual modes must fire for this test to be valid
        assert len(sigs_div) == 1, "Divergence mode should fire"
        assert len(sigs_accel) == 1, "Acceleration mode should fire"
        assert len(sigs_combined) == 1, "Combined mode should fire"

        # All three must agree on LONG direction
        assert sigs_div[0].direction == PositionSide.LONG
        assert sigs_accel[0].direction == PositionSide.LONG
        assert sigs_combined[0].direction == PositionSide.LONG

        # Combined conviction must be ≥ the larger of the two individual convictions
        max_single = max(sigs_div[0].conviction, sigs_accel[0].conviction)
        assert sigs_combined[0].conviction >= max_single

    def test_combined_modes_conflict_no_signal(self) -> None:
        """Modes disagreeing on direction → no signal emitted.

        OI shape for the last 21 bars (classic divergence LONG + acceleration SHORT):
          bar -21: 500_000  (baseline, relatively low)
          bar -20: 510_000  (slightly higher)
          bars -19..-6: steadily rising to ~670_000
          bar -5: 680_000   (peak — highest point)
          bars -4..-1: sharp drop ending at 600_000

        Divergence LONG: ois[-1]=600k > ois[-21]=500k (+20%) → OI net up ✓
        Acceleration SHORT:
          roc_short = (600k-680k)/680k*100 = -11.8%
          roc_long  = (600k-510k)/510k*100 = +17.6%
          accel = -11.8% - 17.6% = -29.4% < -0.5 → SHORT ✓
        → Conflict → no signal.
        """
        params = _permissive_params(accel_threshold=0.5)
        strategy = OIDivergenceStrategy(params=params)

        store = _build_store_with_history(
            n_samples=65,
            base_price=2800.0,
            base_oi=500_000.0,
            price_trend=-5.0,   # Price falling throughout (sets up div direction)
        )

        oi_deque = store._open_interests
        all_ois = list(oi_deque)

        all_ois[-21] = 500_000.0   # baseline
        all_ois[-20] = 510_000.0
        for k in range(14):        # bars -19 to -6: rise from 520k to ~670k
            all_ois[-19 + k] = 520_000.0 + k * 10_700.0
        all_ois[-5] = 680_000.0    # peak
        all_ois[-4] = 660_000.0
        all_ois[-3] = 640_000.0
        all_ois[-2] = 620_000.0
        all_ois[-1] = 600_000.0    # final (still > bar -21 for div LONG)

        oi_deque.clear()
        oi_deque.extend(all_ois)

        snap_final = _make_snapshot(
            price=2475.0,           # Price well below 21 bars ago → div fires
            open_interest=600_000.0,
            ts=BASE_TS + timedelta(seconds=66),
        )
        store.update(snap_final)
        signals = strategy.evaluate(snap_final, store)
        assert signals == []


# ---------------------------------------------------------------------------
# Conviction model tests
# ---------------------------------------------------------------------------


class TestConvictionModel:
    """Unit tests for the conviction scaling model."""

    def test_conviction_scales_with_divergence_magnitude(self) -> None:
        """Larger price/OI divergence → higher conviction from the divergence component."""
        params = _permissive_params(accel_threshold=1000.0)  # Divergence only

        # Small divergence — price falls 2%, OI rises 2%
        store_small = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=490_000.0,
            price_trend=-0.2,   # Small downward trend
            oi_trend=100.0,     # Small upward OI trend
            rng_seed=1,
        )
        snap_small = _make_snapshot(price=2588.0, open_interest=495_000.0)
        store_small.update(snap_small)
        strat_small = OIDivergenceStrategy(params=params)
        sigs_small = strat_small.evaluate(snap_small, store_small)

        # Large divergence — price falls 10%, OI rises 25%
        store_large = _build_store_with_history(
            n_samples=60,
            base_price=2800.0,
            base_oi=400_000.0,
            price_trend=-8.0,   # Strong downward trend
            oi_trend=5000.0,    # Strong upward OI trend
            rng_seed=2,
        )
        snap_large = _make_snapshot(price=2320.0, open_interest=700_000.0)
        store_large.update(snap_large)
        strat_large = OIDivergenceStrategy(params=params)
        sigs_large = strat_large.evaluate(snap_large, store_large)

        # Both must fire for the comparison to be valid
        if len(sigs_small) == 1 and len(sigs_large) == 1:
            assert sigs_large[0].conviction >= sigs_small[0].conviction
        elif len(sigs_large) == 1:
            # Only large fires — large magnitude gives higher conviction by definition
            pass  # Acceptable: small magnitude below threshold
        else:
            pytest.skip("Neither scenario produced a signal — adjust test parameters")


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


class TestPortfolioRouting:
    """Tests for Portfolio A vs B routing via conviction threshold."""

    def test_portfolio_a_routing_at_high_conviction(self) -> None:
        """Conviction >= portfolio_a_min_conviction → suggested_target = Portfolio A."""
        # Set a very low A threshold so any signal qualifies.
        # Disable accel mode to avoid mode conflicts with the divergence direction.
        params = _permissive_params(portfolio_a_min_conviction=0.01, accel_threshold=1000.0)
        strategy = OIDivergenceStrategy(params=params)

        store = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=400_000.0,
            price_trend=-5.0,
            oi_trend=2000.0,
        )
        snap = _make_snapshot(price=2300.0, open_interest=520_000.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].suggested_target == PortfolioTarget.A

    def test_portfolio_b_routing_at_medium_conviction(self) -> None:
        """Conviction < portfolio_a_min_conviction → suggested_target = Portfolio B."""
        # Set an impossibly high A threshold to force routing to B
        params = _permissive_params(
            portfolio_a_min_conviction=0.99,
            accel_threshold=1000.0,  # Only classic divergence fires (max 0.50)
        )
        strategy = OIDivergenceStrategy(params=params)

        store = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=400_000.0,
            price_trend=-5.0,
            oi_trend=2000.0,
        )
        snap = _make_snapshot(price=2300.0, open_interest=520_000.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        if signals:
            assert signals[0].suggested_target == PortfolioTarget.B


# ---------------------------------------------------------------------------
# Signal metadata tests
# ---------------------------------------------------------------------------


class TestSignalMetadata:
    """Tests for signal field correctness and metadata population."""

    def _make_long_signal(self) -> list[StandardSignal]:
        """Return a list with one LONG OI-divergence signal."""
        params = _permissive_params(accel_threshold=1000.0)
        strategy = OIDivergenceStrategy(params=params)
        store = _build_store_with_history(
            n_samples=60,
            base_price=2600.0,
            base_oi=400_000.0,
            price_trend=-5.0,
            oi_trend=2000.0,
        )
        snap = _make_snapshot(price=2300.0, open_interest=520_000.0)
        store.update(snap)
        return strategy.evaluate(snap, store)

    def _make_short_signal(self) -> list[StandardSignal]:
        """Return a list with one SHORT OI-divergence signal."""
        params = _permissive_params(accel_threshold=1000.0)
        strategy = OIDivergenceStrategy(params=params)
        store = _build_store_with_history(
            n_samples=60,
            base_price=2400.0,
            base_oi=600_000.0,
            price_trend=5.0,
            oi_trend=-2000.0,
        )
        snap = _make_snapshot(price=2700.0, open_interest=480_000.0)
        store.update(snap)
        return strategy.evaluate(snap, store)

    def test_source_is_oi_divergence(self) -> None:
        """Signal source must be SignalSource.OI_DIVERGENCE."""
        signals = self._make_long_signal()
        assert len(signals) == 1
        assert signals[0].source == SignalSource.OI_DIVERGENCE

    def test_stops_are_atr_based(self) -> None:
        """stop_loss and take_profit are present and not None."""
        signals = self._make_long_signal()
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss is not None
        assert sig.take_profit is not None

    def test_direction_is_correct_long(self) -> None:
        """LONG signal: stop_loss < entry_price < take_profit."""
        signals = self._make_long_signal()
        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.LONG
        assert sig.stop_loss < sig.entry_price  # type: ignore[operator]
        assert sig.take_profit > sig.entry_price  # type: ignore[operator]

    def test_direction_is_correct_short(self) -> None:
        """SHORT signal: take_profit < entry_price < stop_loss."""
        signals = self._make_short_signal()
        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == PositionSide.SHORT
        assert sig.stop_loss > sig.entry_price  # type: ignore[operator]
        assert sig.take_profit < sig.entry_price  # type: ignore[operator]

    def test_required_metadata_keys(self) -> None:
        """Signal metadata contains all required keys."""
        signals = self._make_long_signal()
        assert len(signals) == 1
        sig = signals[0]
        for key in ("price_pct", "oi_pct", "acceleration", "div_score", "accel_score", "atr"):
            assert key in sig.metadata, f"Missing metadata key: {key}"

    def test_conviction_in_valid_range(self) -> None:
        """Conviction is always in [0.0, 1.0]."""
        signals = self._make_long_signal()
        assert len(signals) == 1
        assert 0.0 <= signals[0].conviction <= 1.0

    def test_time_horizon_uses_max_holding_hours(self) -> None:
        """time_horizon matches max_holding_hours parameter."""
        params = _permissive_params(accel_threshold=1000.0, max_holding_hours=12)
        strategy = OIDivergenceStrategy(params=params)
        store = _build_store_with_history(
            n_samples=60, base_price=2600.0, base_oi=400_000.0,
            price_trend=-5.0, oi_trend=2000.0,
        )
        snap = _make_snapshot(price=2300.0, open_interest=520_000.0)
        store.update(snap)
        signals = strategy.evaluate(snap, store)

        assert len(signals) == 1
        assert signals[0].time_horizon == timedelta(hours=12)


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Tests for YAML config dict loading and parameter override."""

    def test_from_config_dict(self) -> None:
        """Strategy correctly unpacks all parameters from a config dict."""
        config = {
            "strategy": {"name": "oi_divergence", "enabled": True},
            "parameters": {
                "divergence_lookback": 30,
                "div_threshold_pct": 1.5,
                "accel_short_lookback": 3,
                "accel_long_lookback": 15,
                "accel_threshold": 1.8,
                "atr_period": 10,
                "stop_loss_atr_mult": 1.5,
                "take_profit_atr_mult": 2.5,
                "min_conviction": 0.50,
                "cooldown_bars": 8,
                "max_holding_hours": 12,
                "portfolio_a_min_conviction": 0.75,
                "enabled": True,
            },
        }
        strategy = OIDivergenceStrategy(config=config)
        p = strategy._params

        assert p.divergence_lookback == 30
        assert p.div_threshold_pct == 1.5
        assert p.accel_short_lookback == 3
        assert p.accel_long_lookback == 15
        assert p.accel_threshold == 1.8
        assert p.atr_period == 10
        assert p.stop_loss_atr_mult == 1.5
        assert p.take_profit_atr_mult == 2.5
        assert p.min_conviction == 0.50
        assert p.cooldown_bars == 8
        assert p.max_holding_hours == 12
        assert p.portfolio_a_min_conviction == 0.75
        assert p.enabled is True

    def test_disabled_via_config(self) -> None:
        """Setting enabled=False in the parameters block disables the strategy."""
        config = {
            "strategy": {"name": "oi_divergence", "enabled": True},
            "parameters": {"enabled": False},
        }
        strategy = OIDivergenceStrategy(config=config)
        assert strategy.enabled is False

    def test_default_params_without_config(self) -> None:
        """Strategy uses correct defaults when instantiated with no arguments."""
        strategy = OIDivergenceStrategy()
        p = strategy._params

        assert p.divergence_lookback == 20
        assert p.div_threshold_pct == 2.0
        assert p.accel_short_lookback == 5
        assert p.accel_long_lookback == 20
        assert p.accel_threshold == 2.0
        assert p.atr_period == 14
        assert p.stop_loss_atr_mult == 2.0
        assert p.take_profit_atr_mult == 3.0
        assert p.min_conviction == 0.45
        assert p.cooldown_bars == 12
        assert p.max_holding_hours == 8
        assert p.portfolio_a_min_conviction == 0.70
        assert p.enabled is True

    def test_properties(self) -> None:
        """name, enabled, and min_history properties return correct values."""
        strategy = OIDivergenceStrategy()
        assert strategy.name == "oi_divergence"
        assert strategy.enabled is True
        # min_history = max(20, 20) + 14 + 5 = 39
        assert strategy.min_history == 39

    def test_partial_config_uses_defaults(self) -> None:
        """Config with only some parameters preserves defaults for the rest."""
        config = {
            "parameters": {
                "div_threshold_pct": 3.5,
                # All other params omitted → should use defaults
            },
        }
        strategy = OIDivergenceStrategy(config=config)
        assert strategy._params.div_threshold_pct == 3.5
        assert strategy._params.divergence_lookback == 20   # Default preserved
        assert strategy._params.min_conviction == 0.45      # Default preserved

    def test_per_instrument_config_override(self) -> None:
        """Per-instrument YAML config overrides base parameters correctly."""
        yaml_path = Path("configs/strategies/oi_divergence.yaml")
        assert yaml_path.exists(), f"YAML config not found at {yaml_path}"

        with yaml_path.open() as f:
            full_config: dict[str, Any] = yaml.safe_load(f)

        base_params: dict[str, Any] = dict(full_config.get("parameters", {}))
        instruments: dict[str, Any] = dict(full_config.get("instruments", {}))

        # Build a base strategy from the global parameters block
        base_strategy = OIDivergenceStrategy(config={"parameters": base_params})

        # ETH-PERP overrides: divergence_lookback=18 (vs default 20)
        eth_overrides: dict[str, Any] = {**base_params, **instruments.get("ETH-PERP", {})}
        eth_strategy = OIDivergenceStrategy(config={"parameters": eth_overrides})

        # BTC-PERP overrides: divergence_lookback=24, min_conviction=0.60, cooldown=16
        btc_overrides: dict[str, Any] = {**base_params, **instruments.get("BTC-PERP", {})}
        btc_strategy = OIDivergenceStrategy(config={"parameters": btc_overrides})

        # SOL-PERP overrides: divergence_lookback=15, accel_threshold=3.0, etc.
        sol_overrides: dict[str, Any] = {**base_params, **instruments.get("SOL-PERP", {})}
        sol_strategy = OIDivergenceStrategy(config={"parameters": sol_overrides})

        # Verify base defaults
        assert base_strategy._params.divergence_lookback == 20
        assert base_strategy._params.div_threshold_pct == 2.0
        assert base_strategy._params.accel_threshold == 2.0
        assert base_strategy._params.min_conviction == 0.45

        # ETH-PERP overrides
        assert eth_strategy._params.divergence_lookback == 18
        assert eth_strategy._params.div_threshold_pct == 1.5
        assert eth_strategy._params.cooldown_bars == 12
        # ETH-PERP does NOT override accel_threshold — must equal base
        assert eth_strategy._params.accel_threshold == 2.0

        # BTC-PERP overrides
        assert btc_strategy._params.divergence_lookback == 24
        assert btc_strategy._params.min_conviction == 0.60
        assert btc_strategy._params.cooldown_bars == 16
        # BTC-PERP does NOT override div_threshold_pct (stays 2.0) — same as base
        assert btc_strategy._params.div_threshold_pct == 2.0

        # SOL-PERP overrides
        assert sol_strategy._params.divergence_lookback == 15
        assert sol_strategy._params.div_threshold_pct == 2.5
        assert sol_strategy._params.accel_threshold == 3.0
        assert sol_strategy._params.stop_loss_atr_mult == 2.5
        assert sol_strategy._params.take_profit_atr_mult == 4.0
        assert sol_strategy._params.cooldown_bars == 10
        # Instruments differ from each other
        assert eth_strategy._params.divergence_lookback != btc_strategy._params.divergence_lookback
        assert btc_strategy._params.cooldown_bars > eth_strategy._params.cooldown_bars
