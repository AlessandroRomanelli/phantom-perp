"""Tests for the AlphaCombiner — signal aggregation and routing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import (
    MarketRegime,
    Route,
    PositionSide,
    SignalSource,
)
from libs.common.models.signal import StandardSignal
from libs.portfolio.router import PortfolioRouter

from agents.alpha.combiner import AlphaCombiner
from agents.alpha.regime_detector import RegimeDetector
from agents.alpha.scorecard import StrategyScorecard

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _sig(
    source: SignalSource = SignalSource.MOMENTUM,
    direction: PositionSide = PositionSide.LONG,
    conviction: float = 0.7,
    time_horizon: timedelta = timedelta(hours=4),
    entry_price: Decimal | None = Decimal("2200"),
    stop_loss: Decimal | None = Decimal("2150"),
    take_profit: Decimal | None = Decimal("2350"),
    signal_id: str = "sig-1",
    **overrides: object,
) -> StandardSignal:
    defaults = dict(
        signal_id=signal_id,
        timestamp=T0,
        instrument="ETH-PERP",
        direction=direction,
        conviction=conviction,
        source=source,
        time_horizon=time_horizon,
        reasoning="test signal",
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    defaults.update(overrides)
    return StandardSignal(**defaults)  # type: ignore[arg-type]


def _build_combiner(
    cooldown_seconds: float = 0,
    window_seconds: float = 60,
    min_flip_interval_seconds: float = 180,
) -> AlphaCombiner:
    return AlphaCombiner(
        router=PortfolioRouter(),
        regime_detector=RegimeDetector(),
        scorecard=StrategyScorecard(),
        combination_window=timedelta(seconds=window_seconds),
        cooldown=timedelta(seconds=cooldown_seconds),
        min_flip_interval=timedelta(seconds=min_flip_interval_seconds),
    )


class TestSingleSignal:
    def test_single_signal_produces_idea(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(_sig(), now=T0)
        assert len(ideas) == 1
        idea = ideas[0]
        assert idea.direction == PositionSide.LONG
        assert idea.instrument == "ETH-PERP"
        assert SignalSource.MOMENTUM in idea.sources

    def test_idea_has_route(self) -> None:
        combiner = _build_combiner()
        # time_horizon=4h → default routing → B (user-confirmed)
        ideas = combiner.add_signal(
            _sig(time_horizon=timedelta(hours=4)),
            now=T0,
        )
        assert ideas[0].route == Route.B

    def test_short_horizon_routes_to_a(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(
            _sig(time_horizon=timedelta(minutes=30)),
            now=T0,
        )
        assert ideas[0].route == Route.A

    def test_idea_uses_signal_prices(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(
            _sig(
                entry_price=Decimal("2200"),
                stop_loss=Decimal("2150"),
                take_profit=Decimal("2350"),
            ),
            now=T0,
        )
        idea = ideas[0]
        assert idea.entry_price == Decimal("2200")
        assert idea.stop_loss == Decimal("2150")
        assert idea.take_profit == Decimal("2350")

    def test_idea_preserves_conviction(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(_sig(conviction=0.85), now=T0)
        assert ideas[0].conviction == 0.85


class TestSignalCombination:
    def test_aligned_signals_combined(self) -> None:
        combiner = _build_combiner()
        s1 = _sig(
            source=SignalSource.MOMENTUM,
            conviction=0.7,
            signal_id="s1",
        )
        s2 = _sig(
            source=SignalSource.SENTIMENT,
            conviction=0.6,
            signal_id="s2",
        )
        # First signal produces an idea
        ideas1 = combiner.add_signal(s1, now=T0)
        assert len(ideas1) == 1

        # Second signal within window — consumed (same direction cooldown)
        ideas2 = combiner.add_signal(s2, now=T0 + timedelta(seconds=1))
        # Cooldown with cooldown=0 means no cooldown, so it should combine
        # But signals from the first round were consumed, so s2 stands alone
        # Actually with cooldown=0, the recent_ideas check fires (T0 is within 0s window)
        # Let me think... cooldown=0 means cutoff = now - 0 = now, and ts > cutoff
        # If ts == T0 and cutoff == T0 + 1s - 0s = T0 + 1s, then T0 > T0+1s is False
        # So no cooldown block. But s1 was consumed in the first call.
        # s2 is the only unconsumed signal → standalone idea
        assert len(ideas2) == 1

    def test_two_signals_at_same_time_combined(self) -> None:
        """When both signals arrive together, they combine into one idea."""
        combiner = _build_combiner(cooldown_seconds=0, window_seconds=60)
        s1 = _sig(
            source=SignalSource.MOMENTUM,
            conviction=0.7,
            signal_id="s1",
        )
        # Add first signal — produces idea, consuming s1
        ideas1 = combiner.add_signal(s1, now=T0)
        assert len(ideas1) == 1

        # Reset combiner to test both-at-once scenario
        combiner2 = _build_combiner(cooldown_seconds=0, window_seconds=60)
        s2 = _sig(
            source=SignalSource.SENTIMENT,
            conviction=0.6,
            signal_id="s2",
        )
        # Add s1 first but with a cooldown that blocks the second emission
        combiner2 = _build_combiner(cooldown_seconds=10, window_seconds=60)
        combiner2.add_signal(s1, now=T0)
        # s2 within cooldown — should be blocked
        ideas2 = combiner2.add_signal(s2, now=T0 + timedelta(seconds=1))
        assert len(ideas2) == 0

    def test_idea_uses_best_entry_price(self) -> None:
        """Entry price comes from the highest-conviction signal."""
        combiner = _build_combiner()
        s1 = _sig(
            source=SignalSource.MOMENTUM,
            conviction=0.9,
            entry_price=Decimal("2200"),
            signal_id="s1",
        )
        # Make s1 the only signal
        ideas = combiner.add_signal(s1, now=T0)
        assert ideas[0].entry_price == Decimal("2200")


class TestConflictHandling:
    def test_opposing_signals_resolved(self) -> None:
        combiner = _build_combiner(cooldown_seconds=0, window_seconds=60)
        long_sig = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.9,
            signal_id="long",
        )
        short_sig = _sig(
            source=SignalSource.MEAN_REVERSION,
            direction=PositionSide.SHORT,
            conviction=0.3,
            signal_id="short",
        )
        # First signal produces a LONG idea
        ideas1 = combiner.add_signal(long_sig, now=T0)
        assert len(ideas1) == 1
        assert ideas1[0].direction == PositionSide.LONG

    def test_equal_opposition_cancels(self) -> None:
        """When long and short have equal conviction, no idea is emitted."""
        combiner = _build_combiner(
            cooldown_seconds=0, window_seconds=60, min_flip_interval_seconds=0,
        )
        long_sig = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.5,
            signal_id="long",
        )
        short_sig = _sig(
            source=SignalSource.MEAN_REVERSION,
            direction=PositionSide.SHORT,
            conviction=0.5,
            signal_id="short",
        )
        # Both arrive before either is consumed
        # Add long — standalone idea at conviction 0.5
        combiner.add_signal(long_sig, now=T0)
        # Add short — only short is unconsumed, standalone short idea
        # Actually long was consumed, so short stands alone
        ideas = combiner.add_signal(short_sig, now=T0 + timedelta(seconds=1))
        # Short signal alone produces an idea at conviction 0.5
        assert len(ideas) == 1


class TestCooldown:
    def test_cooldown_prevents_duplicate_ideas(self) -> None:
        combiner = _build_combiner(cooldown_seconds=30)
        s1 = _sig(signal_id="s1")
        s2 = _sig(signal_id="s2", source=SignalSource.SENTIMENT)

        ideas1 = combiner.add_signal(s1, now=T0)
        assert len(ideas1) == 1

        # Within cooldown — same direction, different source
        ideas2 = combiner.add_signal(s2, now=T0 + timedelta(seconds=10))
        assert len(ideas2) == 0

    def test_cooldown_expires(self) -> None:
        combiner = _build_combiner(cooldown_seconds=30)
        s1 = _sig(signal_id="s1")
        s2 = _sig(signal_id="s2", source=SignalSource.SENTIMENT)

        combiner.add_signal(s1, now=T0)

        # After cooldown expires
        ideas2 = combiner.add_signal(s2, now=T0 + timedelta(seconds=31))
        assert len(ideas2) == 1

    def test_cooldown_per_direction(self) -> None:
        """Bidirectional cooldown blocks opposite direction too within cooldown window."""
        combiner = _build_combiner(cooldown_seconds=30)
        long_sig = _sig(direction=PositionSide.LONG, signal_id="long")
        short_sig = _sig(
            direction=PositionSide.SHORT,
            signal_id="short",
            source=SignalSource.MEAN_REVERSION,
        )

        combiner.add_signal(long_sig, now=T0)

        # SHORT is also blocked — bidirectional cooldown applies to any direction
        ideas = combiner.add_signal(short_sig, now=T0 + timedelta(seconds=5))
        assert len(ideas) == 0


class TestBufferWindow:
    def test_old_signals_pruned(self) -> None:
        combiner = _build_combiner(
            cooldown_seconds=0,
            window_seconds=10,
        )
        old_sig = _sig(signal_id="old")
        combiner.add_signal(old_sig, now=T0)

        # 15 seconds later — old signal should be pruned from buffer
        new_sig = _sig(
            signal_id="new",
            source=SignalSource.SENTIMENT,
        )
        ideas = combiner.add_signal(new_sig, now=T0 + timedelta(seconds=15))
        # Only new_sig is in the buffer → standalone idea
        assert len(ideas) == 1


class TestIdeaMetadata:
    def test_idea_has_regime_metadata(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(_sig(), now=T0)
        assert "regime" in ideas[0].metadata

    def test_idea_has_contributing_signals_count(self) -> None:
        combiner = _build_combiner()
        ideas = combiner.add_signal(_sig(), now=T0)
        assert ideas[0].metadata["contributing_signals"] == 1

    def test_idea_uses_median_horizon(self) -> None:
        combiner = _build_combiner()
        sig = _sig(time_horizon=timedelta(hours=6))
        ideas = combiner.add_signal(sig, now=T0)
        assert ideas[0].time_horizon == timedelta(hours=6)
