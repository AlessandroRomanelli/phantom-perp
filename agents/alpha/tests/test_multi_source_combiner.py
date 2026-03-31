"""Integration tests for multi-source alpha combining (QUAL-12).

Proves that:
- Aligned signals from 4+ sources produce a single idea (not N ideas) when cooldown blocks bursts
- Opposing signals from 2 sources are resolved by regime boost
- 12+ concurrent signals across 3 instruments produce at most 1 idea per instrument per cycle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.common.models.enums import (
    MarketRegime,
    Route,
    PositionSide,
    SignalSource,
)
from libs.common.models.signal import StandardSignal
from libs.portfolio.router import RouteRouter

from agents.alpha.combiner import AlphaCombiner
from agents.alpha.regime_detector import RegimeDetector
from agents.alpha.scorecard import StrategyScorecard

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTER = 0


def _unique_id() -> str:
    global _COUNTER
    _COUNTER += 1
    return f"sig-{_COUNTER}"


def _sig(
    source: SignalSource = SignalSource.MOMENTUM,
    direction: PositionSide = PositionSide.LONG,
    conviction: float = 0.7,
    instrument: str = "ETH-PERP",
    time_horizon: timedelta = timedelta(hours=1),
    ts: datetime = T0,
) -> StandardSignal:
    """Build a minimal StandardSignal with a unique ID."""
    return StandardSignal(
        signal_id=_unique_id(),
        timestamp=ts,
        instrument=instrument,
        direction=direction,
        conviction=conviction,
        source=source,
        time_horizon=time_horizon,
        reasoning="integration test signal",
    )


def _build_combiner(
    cooldown_seconds: float = 30,
    window_seconds: float = 120,
    min_flip_interval_seconds: float = 0,
) -> AlphaCombiner:
    """Build a combiner with controllable cooldown for testing.

    Default cooldown_seconds=30 matches production default, which is long enough
    to block burst signals arriving a few seconds apart.
    """
    return AlphaCombiner(
        router=RouteRouter(),
        regime_detector=RegimeDetector(),
        scorecard=StrategyScorecard(),
        combination_window=timedelta(seconds=window_seconds),
        cooldown=timedelta(seconds=cooldown_seconds),
        min_flip_interval=timedelta(seconds=min_flip_interval_seconds),
    )


# ---------------------------------------------------------------------------
# Tests — aligned multi-source signals produce a single idea
# ---------------------------------------------------------------------------

class TestAlignedMultiSourceProducesSingleIdea:
    """4+ aligned LONG signals in rapid succession → exactly 1 trade idea (QUAL-11).

    The bidirectional cooldown (default 30s) blocks ideas 2-4 from signals that
    arrive within 1-second intervals. Only the first signal fires an idea; the
    remaining signals are suppressed by cooldown.
    """

    def test_four_aligned_sources_produce_one_idea(self) -> None:
        """QUAL-11: 4 aligned signals arriving 1s apart with 30s cooldown → 1 idea."""
        # 30s cooldown blocks signals arriving 1 second after the first idea
        combiner = _build_combiner(cooldown_seconds=30)

        sources = [
            SignalSource.MOMENTUM,
            SignalSource.CONTRARIAN_FUNDING,
            SignalSource.OI_DIVERGENCE,
            SignalSource.CLAUDE_MARKET_ANALYSIS,
        ]

        all_ideas: list = []
        for i, source in enumerate(sources):
            sig = _sig(
                source=source,
                direction=PositionSide.LONG,
                conviction=0.7,
                ts=T0 + timedelta(seconds=i),
            )
            ideas = combiner.add_signal(sig, now=T0 + timedelta(seconds=i))
            all_ideas.extend(ideas)

        # Only the first signal fires an idea; the rest are blocked by cooldown
        assert len(all_ideas) == 1, (
            f"Expected 1 trade idea from 4 aligned signals (30s cooldown), got {len(all_ideas)}"
        )
        idea = all_ideas[0]
        assert idea.direction == PositionSide.LONG
        assert idea.instrument == "ETH-PERP"

    def test_four_aligned_sources_agreement_boost_via_resolve(self) -> None:
        """Agreement boost is applied by resolve_conflicts for aligned signals.

        The combiner delegates multi-signal combination to resolve_conflicts.
        We test the boost directly there: 4 aligned signals at 0.65 conviction
        should yield a combined conviction > 0.65 (base weighted-avg + boost).
        """
        from agents.alpha.conflict_resolver import resolve_conflicts

        individual_conviction = 0.65
        sources = [
            SignalSource.MOMENTUM,
            SignalSource.CONTRARIAN_FUNDING,
            SignalSource.OI_DIVERGENCE,
            SignalSource.CLAUDE_MARKET_ANALYSIS,
        ]
        signals = [
            StandardSignal(
                signal_id=f"boost-sig-{i}",
                timestamp=T0,
                instrument="ETH-PERP",
                direction=PositionSide.LONG,
                conviction=individual_conviction,
                source=source,
                time_horizon=timedelta(hours=1),
                reasoning="test",
            )
            for i, source in enumerate(sources)
        ]

        result = resolve_conflicts(signals, MarketRegime.RANGING, StrategyScorecard())

        assert result is not None
        assert result.direction == PositionSide.LONG
        assert result.conviction > individual_conviction, (
            f"Expected conviction boosted above {individual_conviction}, "
            f"got {result.conviction}"
        )

    def test_all_sources_present_in_resolve_result(self) -> None:
        """Sources from all aligned contributing signals appear in resolve_conflicts result."""
        from agents.alpha.conflict_resolver import resolve_conflicts

        sources = [
            SignalSource.MOMENTUM,
            SignalSource.CONTRARIAN_FUNDING,
            SignalSource.VWAP,
            SignalSource.OI_DIVERGENCE,
        ]
        signals = [
            StandardSignal(
                signal_id=f"src-sig-{i}",
                timestamp=T0,
                instrument="ETH-PERP",
                direction=PositionSide.LONG,
                conviction=0.7,
                source=source,
                time_horizon=timedelta(hours=1),
                reasoning="test",
            )
            for i, source in enumerate(sources)
        ]

        result = resolve_conflicts(signals, MarketRegime.RANGING, StrategyScorecard())

        assert result is not None
        for source in sources:
            assert source in result.sources, (
                f"Source {source.value} missing from resolved sources: {result.sources}"
            )


# ---------------------------------------------------------------------------
# Tests — opposing signals resolved by regime boost
# ---------------------------------------------------------------------------

class TestOpposingSignalsRegimeResolution:
    """Conflicting LONG/SHORT signals from new M002 sources are resolved by regime."""

    def test_contrarian_funding_short_loses_to_momentum_long_in_trend(self) -> None:
        """In TRENDING_UP regime, MOMENTUM gets a 1.3x boost vs CONTRARIAN_FUNDING 0.7x.

        With equal conviction (0.6), momentum * 1.3 > contrarian_funding * 0.7,
        so the LONG side wins.
        """
        from agents.alpha.conflict_resolver import resolve_conflicts

        mom_long = StandardSignal(
            signal_id="test-long",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.LONG,
            conviction=0.6,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=2),
            reasoning="momentum bullish",
        )
        funding_short = StandardSignal(
            signal_id="test-short",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.SHORT,
            conviction=0.6,
            source=SignalSource.CONTRARIAN_FUNDING,
            time_horizon=timedelta(hours=2),
            reasoning="funding bearish",
        )

        result = resolve_conflicts(
            [mom_long, funding_short],
            MarketRegime.TRENDING_UP,
            StrategyScorecard(),
        )

        assert result is not None, "Signals should not cancel — momentum has regime advantage"
        assert result.direction == PositionSide.LONG, (
            "MOMENTUM (1.3x boost) should override CONTRARIAN_FUNDING (0.7x) in TRENDING_UP"
        )

    def test_contrarian_funding_wins_in_ranging_regime(self) -> None:
        """In RANGING regime, CONTRARIAN_FUNDING gets 1.3x vs MOMENTUM 0.7x.

        Equal conviction → contrarian_funding wins.
        """
        from agents.alpha.conflict_resolver import resolve_conflicts

        mom_long = StandardSignal(
            signal_id="test-long-2",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.LONG,
            conviction=0.6,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=2),
            reasoning="momentum bullish",
        )
        funding_short = StandardSignal(
            signal_id="test-short-2",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.SHORT,
            conviction=0.6,
            source=SignalSource.CONTRARIAN_FUNDING,
            time_horizon=timedelta(hours=2),
            reasoning="funding bearish",
        )

        result = resolve_conflicts(
            [mom_long, funding_short],
            MarketRegime.RANGING,
            StrategyScorecard(),
        )

        assert result is not None
        assert result.direction == PositionSide.SHORT, (
            "CONTRARIAN_FUNDING (1.3x boost) should override MOMENTUM (0.7x) in RANGING"
        )

    def test_oi_divergence_boosted_in_high_volatility(self) -> None:
        """In HIGH_VOLATILITY, OI_DIVERGENCE (1.3x) beats MOMENTUM (0.8x) when equal conviction."""
        from agents.alpha.conflict_resolver import resolve_conflicts

        mom_long = StandardSignal(
            signal_id="test-long-3",
            timestamp=T0,
            instrument="BTC-PERP",
            direction=PositionSide.LONG,
            conviction=0.6,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=1),
            reasoning="momentum bullish",
        )
        oi_short = StandardSignal(
            signal_id="test-short-3",
            timestamp=T0,
            instrument="BTC-PERP",
            direction=PositionSide.SHORT,
            conviction=0.6,
            source=SignalSource.OI_DIVERGENCE,
            time_horizon=timedelta(hours=1),
            reasoning="OI divergence bearish",
        )

        result = resolve_conflicts(
            [mom_long, oi_short],
            MarketRegime.HIGH_VOLATILITY,
            StrategyScorecard(),
        )

        assert result is not None
        assert result.direction == PositionSide.SHORT, (
            "OI_DIVERGENCE (1.3x boost) should override MOMENTUM (0.8x) in HIGH_VOLATILITY"
        )

    def test_claude_neutral_does_not_flip_outcome(self) -> None:
        """CLAUDE_MARKET_ANALYSIS is regime-neutral (1.0x), so it should not change the winner."""
        from agents.alpha.conflict_resolver import resolve_conflicts

        # Strong momentum long (conviction=0.8)
        mom_long = StandardSignal(
            signal_id="mom-long",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.LONG,
            conviction=0.8,
            source=SignalSource.MOMENTUM,
            time_horizon=timedelta(hours=2),
            reasoning="momentum bullish",
        )
        # Weak Claude short (conviction=0.4, regime=1.0)
        claude_short = StandardSignal(
            signal_id="claude-short",
            timestamp=T0,
            instrument="ETH-PERP",
            direction=PositionSide.SHORT,
            conviction=0.4,
            source=SignalSource.CLAUDE_MARKET_ANALYSIS,
            time_horizon=timedelta(hours=2),
            reasoning="claude bearish",
        )

        # In TRENDING_UP: momentum*1.3*0.8=1.04, claude*1.0*0.4=0.4 → LONG wins
        result = resolve_conflicts(
            [mom_long, claude_short],
            MarketRegime.TRENDING_UP,
            StrategyScorecard(),
        )
        assert result is not None
        assert result.direction == PositionSide.LONG


# ---------------------------------------------------------------------------
# Tests — 12+ concurrent signals across 3 instruments
# ---------------------------------------------------------------------------

class TestMultiInstrumentBurst:
    """QUAL-11: 12+ signals across 3 instruments → at most 1 idea per instrument per cycle."""

    def _feed_instrument_signals(
        self,
        combiner: AlphaCombiner,
        instrument: str,
        n: int = 4,
        direction: PositionSide = PositionSide.LONG,
        start_offset_seconds: float = 0,
        gap_seconds: float = 1,
    ) -> list:
        """Feed n aligned signals for a single instrument and return all ideas produced."""
        sources = [
            SignalSource.MOMENTUM,
            SignalSource.CONTRARIAN_FUNDING,
            SignalSource.OI_DIVERGENCE,
            SignalSource.CLAUDE_MARKET_ANALYSIS,
            SignalSource.VWAP,
            SignalSource.MEAN_REVERSION,
        ]
        all_ideas: list = []
        for i in range(n):
            offset = start_offset_seconds + i * gap_seconds
            sig = _sig(
                source=sources[i % len(sources)],
                direction=direction,
                conviction=0.7,
                instrument=instrument,
                ts=T0 + timedelta(seconds=offset),
            )
            ideas = combiner.add_signal(sig, now=T0 + timedelta(seconds=offset))
            all_ideas.extend(ideas)
        return all_ideas

    def test_twelve_signals_across_three_instruments_with_cooldown(self) -> None:
        """QUAL-11: 12 signals (4 per instrument, 3 instruments) with 30s cooldown → 3 ideas max.

        With a 30-second cooldown and 1-second signal gaps, each instrument emits
        exactly 1 idea (the first signal fires, the rest are blocked by cooldown).
        """
        combiner = _build_combiner(cooldown_seconds=30)

        eth_ideas = self._feed_instrument_signals(combiner, "ETH-PERP", n=4)
        btc_ideas = self._feed_instrument_signals(combiner, "BTC-PERP", n=4, start_offset_seconds=10)
        sol_ideas = self._feed_instrument_signals(combiner, "SOL-PERP", n=4, start_offset_seconds=20)

        # Each instrument produces at most 1 idea (30s cooldown blocks remaining 3 signals)
        assert len(eth_ideas) <= 1, (
            f"ETH-PERP: expected at most 1 idea from 4 signals (30s cooldown), got {len(eth_ideas)}"
        )
        assert len(btc_ideas) <= 1, (
            f"BTC-PERP: expected at most 1 idea from 4 signals (30s cooldown), got {len(btc_ideas)}"
        )
        assert len(sol_ideas) <= 1, (
            f"SOL-PERP: expected at most 1 idea from 4 signals (30s cooldown), got {len(sol_ideas)}"
        )

        total_ideas = len(eth_ideas) + len(btc_ideas) + len(sol_ideas)
        assert total_ideas <= 3, (
            f"Expected at most 3 total ideas across 3 instruments, got {total_ideas}"
        )

    def test_instruments_do_not_share_cooldown(self) -> None:
        """A cooldown from ETH must not suppress ideas for BTC."""
        combiner = _build_combiner(cooldown_seconds=60)

        # ETH first — triggers a cooldown for ETH
        eth_ideas = self._feed_instrument_signals(combiner, "ETH-PERP", n=1)
        assert len(eth_ideas) == 1, "ETH should produce 1 idea"

        # BTC at the same timestamp — its own cooldown is fresh, so it fires
        btc_ideas = self._feed_instrument_signals(combiner, "BTC-PERP", n=1, start_offset_seconds=0)
        assert len(btc_ideas) == 1, (
            "BTC-PERP should produce its own idea — ETH cooldown must not bleed over"
        )

    def test_single_instrument_cooldown_blocks_burst(self) -> None:
        """QUAL-11: After one idea for ETH, further signals within cooldown produce no new ideas."""
        combiner = _build_combiner(cooldown_seconds=60)

        # First signal produces an idea
        first_ideas = self._feed_instrument_signals(combiner, "ETH-PERP", n=1)
        assert len(first_ideas) == 1

        # Signals 1 second later for ETH — should be blocked by 60s cooldown
        more_ideas = self._feed_instrument_signals(
            combiner,
            "ETH-PERP",
            n=4,
            direction=PositionSide.LONG,
            start_offset_seconds=1,
        )
        assert len(more_ideas) == 0, (
            f"Expected 0 new ideas within 60s cooldown, got {len(more_ideas)}"
        )

    def test_opposite_direction_flip_guard_blocks_reversal(self) -> None:
        """QUAL-11: Flip guard prevents direction reversal within min_flip_interval."""
        # cooldown=0 so signals can produce ideas, but flip guard=60s blocks direction reversal
        combiner = _build_combiner(cooldown_seconds=0, min_flip_interval_seconds=60)

        # LONG signal produces 1 idea
        long_sig = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.7,
            ts=T0,
        )
        long_ideas = combiner.add_signal(long_sig, now=T0)
        assert len(long_ideas) == 1, "LONG signal should produce 1 idea"

        # SHORT signal 5 seconds later → flip guard active (60s window) → blocked
        short_sig = _sig(
            source=SignalSource.CONTRARIAN_FUNDING,
            direction=PositionSide.SHORT,
            conviction=0.9,  # even high conviction is blocked by flip guard
            ts=T0 + timedelta(seconds=5),
        )
        short_ideas = combiner.add_signal(short_sig, now=T0 + timedelta(seconds=5))
        assert len(short_ideas) == 0, (
            f"SHORT should be blocked by flip guard (60s), got {len(short_ideas)} ideas"
        )

        # SHORT signal 70 seconds later → flip guard expired → should fire
        short_sig_late = _sig(
            source=SignalSource.CONTRARIAN_FUNDING,
            direction=PositionSide.SHORT,
            conviction=0.9,
            ts=T0 + timedelta(seconds=70),
        )
        short_ideas_late = combiner.add_signal(short_sig_late, now=T0 + timedelta(seconds=70))
        assert len(short_ideas_late) == 1, (
            f"SHORT after flip guard expires (70s) should produce 1 idea, "
            f"got {len(short_ideas_late)}"
        )
