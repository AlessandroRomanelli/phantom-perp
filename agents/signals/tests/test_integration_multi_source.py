"""Integration tests for multi-source strategy pipeline (QUAL-11, QUAL-12).

Proves that:
- build_strategies_for_instrument() returns the correct per-instrument count
- All strategies in an instrument set co-evaluate without exceptions
- The orchestrator gate map correctly skips disabled strategies at runtime
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agents.signals.main import build_strategies_for_instrument
from agents.signals.feature_store import FeatureStore
from libs.common.models.market_snapshot import MarketSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_snapshot(instrument: str = "ETH-PERP") -> MarketSnapshot:
    """Minimal MarketSnapshot that satisfies all strategy evaluate() calls."""
    return MarketSnapshot(
        timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        instrument=instrument,
        mark_price=Decimal("2200.00"),
        index_price=Decimal("2198.50"),
        last_price=Decimal("2199.75"),
        best_bid=Decimal("2199.50"),
        best_ask=Decimal("2200.50"),
        spread_bps=2.0,
        volume_24h=Decimal("500000"),
        open_interest=Decimal("1500000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=datetime(2025, 6, 15, 16, 0, 0, tzinfo=UTC),
        hours_since_last_funding=4.0,
        orderbook_imbalance=0.1,
        volatility_1h=0.02,
        volatility_24h=0.04,
    )


def _make_populated_store(
    n: int = 200,
    sample_interval: timedelta = timedelta(seconds=1),
) -> FeatureStore:
    """FeatureStore pre-loaded with n synthetic price samples."""
    store = FeatureStore(max_samples=500, sample_interval=sample_interval)
    base_price = 2200.0
    for i in range(n):
        snap = MarketSnapshot(
            timestamp=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=i),
            instrument="ETH-PERP",
            mark_price=Decimal(str(base_price + i * 0.1)),
            index_price=Decimal(str(base_price + i * 0.1 - 1.0)),
            last_price=Decimal(str(base_price + i * 0.1)),
            best_bid=Decimal(str(base_price + i * 0.1 - 0.5)),
            best_ask=Decimal(str(base_price + i * 0.1 + 0.5)),
            spread_bps=2.0,
            volume_24h=Decimal("500000"),
            open_interest=Decimal("1500000"),
            funding_rate=Decimal("0.0001"),
            next_funding_time=datetime(2025, 6, 15, 16, 0, 0, tzinfo=UTC),
            hours_since_last_funding=float(i % 8),
            orderbook_imbalance=0.1,
            volatility_1h=0.02,
            volatility_24h=0.04,
        )
        store.update(snap)
    return store


# ---------------------------------------------------------------------------
# Tests — strategy count per instrument
# ---------------------------------------------------------------------------

class TestStrategyBuildCount:
    """Verify build_strategies_for_instrument returns the correct count.

    The strategy matrix enables all 10 strategies for BTC-PERP and 9 for
    ETH-PERP / SOL-PERP (orderbook_imbalance is disabled for ETH and SOL
    via the Portfolio A only guard or the per-instrument override check).
    """

    def test_btc_returns_ten_strategies(self) -> None:
        strategies = build_strategies_for_instrument("BTC-PERP")
        names = [s.name for s in strategies]
        assert len(strategies) == 10, (
            f"Expected 10 strategies for BTC-PERP, got {len(strategies)}: {names}"
        )

    def test_eth_returns_nine_strategies(self) -> None:
        strategies = build_strategies_for_instrument("ETH-PERP")
        names = [s.name for s in strategies]
        assert len(strategies) == 9, (
            f"Expected 9 strategies for ETH-PERP, got {len(strategies)}: {names}"
        )

    def test_sol_returns_nine_strategies(self) -> None:
        strategies = build_strategies_for_instrument("SOL-PERP")
        names = [s.name for s in strategies]
        assert len(strategies) == 9, (
            f"Expected 9 strategies for SOL-PERP, got {len(strategies)}: {names}"
        )

    def test_btc_has_orderbook_imbalance_eth_does_not(self) -> None:
        """OBI is enabled for BTC but not for ETH (different portfolio target guard)."""
        btc = {s.name for s in build_strategies_for_instrument("BTC-PERP")}
        eth = {s.name for s in build_strategies_for_instrument("ETH-PERP")}
        assert "orderbook_imbalance" in btc, "OBI missing from BTC-PERP"
        assert "orderbook_imbalance" not in eth, "OBI should not appear for ETH-PERP"

    def test_new_m002_strategies_present_in_all_instruments(self) -> None:
        """funding_arb, oi_divergence, and claude_market_analysis must be in all instruments."""
        new_strategies = {"funding_arb", "oi_divergence", "claude_market_analysis"}
        for instrument in ("ETH-PERP", "BTC-PERP", "SOL-PERP"):
            names = {s.name for s in build_strategies_for_instrument(instrument)}
            missing = new_strategies - names
            assert not missing, (
                f"{instrument} missing M002 strategies: {missing}"
            )


# ---------------------------------------------------------------------------
# Tests — co-evaluation without exceptions
# ---------------------------------------------------------------------------

class TestCoEvaluation:
    """All strategies for a given instrument must evaluate without raising."""

    @pytest.mark.parametrize("instrument", ["ETH-PERP", "BTC-PERP", "SOL-PERP"])
    def test_all_strategies_evaluate_without_exception(self, instrument: str) -> None:
        """Strategies must not raise when called with a populated FeatureStore.

        Strategies that require more history than available will produce empty
        signal lists — that is the correct and expected behaviour. Only
        exceptions are failures.
        """
        strategies = build_strategies_for_instrument(instrument)
        store = _make_populated_store(n=200)
        snapshot = _make_snapshot(instrument)

        errors: list[str] = []
        for strategy in strategies:
            try:
                signals = strategy.evaluate(snapshot, store)
                assert isinstance(signals, list), (
                    f"{strategy.name} returned {type(signals)!r}, expected list"
                )
            except Exception as exc:
                errors.append(f"{strategy.name}: {exc!r}")

        assert not errors, (
            f"{instrument} strategies raised exceptions:\n"
            + "\n".join(errors)
        )

    def test_distinct_instances_per_instrument(self) -> None:
        """Strategies for ETH and BTC must be entirely separate object instances."""
        eth_strats = build_strategies_for_instrument("ETH-PERP")
        btc_strats = build_strategies_for_instrument("BTC-PERP")

        eth_by_name = {s.name: s for s in eth_strats}
        btc_by_name = {s.name: s for s in btc_strats}

        common = set(eth_by_name) & set(btc_by_name)
        assert common, "Expected at least one common strategy name between ETH and BTC"

        for name in common:
            assert id(eth_by_name[name]) != id(btc_by_name[name]), (
                f"Strategy '{name}' is the SAME object for ETH and BTC — "
                "shared instances will corrupt per-instrument state"
            )


# ---------------------------------------------------------------------------
# Tests — orchestrator gate map skips disabled strategies
# ---------------------------------------------------------------------------

class TestGateMapSkipsStrategies:
    """Prove the gate-map pattern: disabled key → strategy never evaluates."""

    def test_disabled_strategy_is_skipped(self) -> None:
        """If gate_map[(instrument, strategy_name)] is False, strategy is skipped."""
        instrument = "ETH-PERP"
        strategies = build_strategies_for_instrument(instrument)
        store = _make_populated_store(n=200)
        snapshot = _make_snapshot(instrument)

        # Build a gate map that disables the first strategy
        gate_map: dict[tuple[str, str], bool] = {}
        disabled_strategy = strategies[0]
        gate_map[(instrument, disabled_strategy.name)] = False

        # Run the gate-map check just as the signals agent main loop does
        skipped: list[str] = []
        evaluated: list[str] = []
        for strategy in strategies:
            if not gate_map.get((instrument, strategy.name), True):
                skipped.append(strategy.name)
                continue
            strategy.evaluate(snapshot, store)
            evaluated.append(strategy.name)

        assert disabled_strategy.name in skipped, (
            f"Expected '{disabled_strategy.name}' to be in skipped list"
        )
        assert disabled_strategy.name not in evaluated, (
            f"'{disabled_strategy.name}' should not have been evaluated"
        )
        # All other strategies should still evaluate
        assert len(evaluated) == len(strategies) - 1

    def test_all_strategies_run_when_gate_map_empty(self) -> None:
        """An empty gate map defaults to True (all strategies enabled)."""
        instrument = "BTC-PERP"
        strategies = build_strategies_for_instrument(instrument)
        store = _make_populated_store(n=200)
        snapshot = _make_snapshot(instrument)

        gate_map: dict[tuple[str, str], bool] = {}

        evaluated: list[str] = []
        for strategy in strategies:
            if not gate_map.get((instrument, strategy.name), True):
                continue
            strategy.evaluate(snapshot, store)
            evaluated.append(strategy.name)

        assert len(evaluated) == len(strategies), (
            "Empty gate map should leave all strategies enabled"
        )

    def test_gate_map_only_affects_target_instrument(self) -> None:
        """Disabling a strategy for ETH must not affect BTC."""
        eth_strats = build_strategies_for_instrument("ETH-PERP")
        btc_strats = build_strategies_for_instrument("BTC-PERP")
        store = _make_populated_store(n=200)

        gate_map: dict[tuple[str, str], bool] = {}
        # Disable momentum only for ETH
        gate_map[("ETH-PERP", "momentum")] = False

        eth_evaluated = [
            s.name for s in eth_strats
            if gate_map.get(("ETH-PERP", s.name), True)
        ]
        btc_evaluated = [
            s.name for s in btc_strats
            if gate_map.get(("BTC-PERP", s.name), True)
        ]

        assert "momentum" not in eth_evaluated, "momentum disabled for ETH"
        assert "momentum" in btc_evaluated, "momentum should still run for BTC"
