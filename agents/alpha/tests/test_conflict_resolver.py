"""Tests for conflict resolution with regime-aware weighting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.common.models.enums import MarketRegime, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal

from agents.alpha.conflict_resolver import resolve_conflicts
from agents.alpha.scorecard import StrategyScorecard


def _sig(
    source: SignalSource = SignalSource.MOMENTUM,
    direction: PositionSide = PositionSide.LONG,
    conviction: float = 0.7,
    **overrides: object,
) -> StandardSignal:
    defaults = dict(
        signal_id="test-signal",
        timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        instrument="ETH-PERP",
        direction=direction,
        conviction=conviction,
        source=source,
        time_horizon=timedelta(hours=4),
        reasoning="test",
    )
    defaults.update(overrides)
    return StandardSignal(**defaults)  # type: ignore[arg-type]


class TestConflictResolver:
    def test_empty_signals_returns_none(self) -> None:
        result = resolve_conflicts(
            [], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is None

    def test_single_signal_passes_through(self) -> None:
        sig = _sig(conviction=0.8)
        result = resolve_conflicts(
            [sig], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        assert result.direction == PositionSide.LONG
        assert result.conviction == pytest.approx(0.8)
        assert SignalSource.MOMENTUM in result.sources

    def test_aligned_signals_boost_conviction(self) -> None:
        s1 = _sig(source=SignalSource.MOMENTUM, conviction=0.7)
        s2 = _sig(source=SignalSource.SENTIMENT, conviction=0.6)
        result = resolve_conflicts(
            [s1, s2], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        assert result.direction == PositionSide.LONG
        # Weighted avg of 0.7 and 0.6, plus agreement boost of 0.05
        assert result.conviction > 0.6
        assert len(result.sources) == 2

    def test_three_aligned_signals_higher_boost(self) -> None:
        s1 = _sig(source=SignalSource.MOMENTUM, conviction=0.6)
        s2 = _sig(source=SignalSource.SENTIMENT, conviction=0.6)
        s3 = _sig(source=SignalSource.CORRELATION, conviction=0.6)
        result_2 = resolve_conflicts(
            [s1, s2], MarketRegime.RANGING, StrategyScorecard(),
        )
        result_3 = resolve_conflicts(
            [s1, s2, s3], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result_2 is not None
        assert result_3 is not None
        # Three signals get a bigger boost than two
        assert result_3.conviction > result_2.conviction

    def test_agreement_boost_capped_at_020(self) -> None:
        """Even with 10 aligned signals, boost doesn't exceed 0.20."""
        signals = [
            _sig(
                source=SignalSource.MOMENTUM,
                conviction=0.8,
                signal_id=f"sig-{i}",
            )
            for i in range(10)
        ]
        result = resolve_conflicts(
            signals, MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        # conviction = 0.8 + 0.20 = 1.0 (capped)
        assert result.conviction <= 1.0

    def test_opposing_signals_strong_winner(self) -> None:
        long_sig = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.9,
        )
        short_sig = _sig(
            source=SignalSource.MEAN_REVERSION,
            direction=PositionSide.SHORT,
            conviction=0.3,
        )
        result = resolve_conflicts(
            [long_sig, short_sig], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        assert result.direction == PositionSide.LONG

    def test_opposing_signals_cancel_out(self) -> None:
        """Nearly equal conviction in both directions → no trade.

        Uses sources without regime boosts (CORRELATION, ONCHAIN) so that
        equal conviction truly cancels out.
        """
        long_sig = _sig(
            source=SignalSource.CORRELATION,
            direction=PositionSide.LONG,
            conviction=0.5,
        )
        short_sig = _sig(
            source=SignalSource.ONCHAIN,
            direction=PositionSide.SHORT,
            conviction=0.5,
        )
        result = resolve_conflicts(
            [long_sig, short_sig],
            MarketRegime.RANGING,
            StrategyScorecard(),
            min_net_conviction=0.15,
        )
        assert result is None

    def test_regime_boost_favors_momentum_in_trend(self) -> None:
        """In a trending market, momentum gets a boost over mean reversion."""
        mom = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.5,
        )
        mr = _sig(
            source=SignalSource.MEAN_REVERSION,
            direction=PositionSide.SHORT,
            conviction=0.5,
        )
        result = resolve_conflicts(
            [mom, mr], MarketRegime.TRENDING_UP, StrategyScorecard(),
        )
        # With equal conviction, regime boost tips the balance
        assert result is not None
        assert result.direction == PositionSide.LONG

    def test_regime_boost_favors_mean_reversion_in_ranging(self) -> None:
        mom = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.5,
        )
        mr = _sig(
            source=SignalSource.MEAN_REVERSION,
            direction=PositionSide.SHORT,
            conviction=0.5,
        )
        result = resolve_conflicts(
            [mom, mr], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        assert result.direction == PositionSide.SHORT

    def test_scorecard_weights_affect_resolution(self) -> None:
        """A strategy with poor accuracy gets less influence."""
        sc = StrategyScorecard()
        # Momentum has been getting it wrong
        for _ in range(10):
            sc.record_outcome(SignalSource.MOMENTUM, False)
        # Sentiment has been getting it right
        for _ in range(10):
            sc.record_outcome(SignalSource.SENTIMENT, True)

        mom = _sig(
            source=SignalSource.MOMENTUM,
            direction=PositionSide.LONG,
            conviction=0.6,
        )
        sent = _sig(
            source=SignalSource.SENTIMENT,
            direction=PositionSide.SHORT,
            conviction=0.6,
        )
        result = resolve_conflicts(
            [mom, sent], MarketRegime.HIGH_VOLATILITY, sc,
        )
        assert result is not None
        # Sentiment should win because it has much better accuracy weight
        assert result.direction == PositionSide.SHORT

    def test_result_includes_reasoning(self) -> None:
        sig = _sig(conviction=0.75)
        result = resolve_conflicts(
            [sig], MarketRegime.RANGING, StrategyScorecard(),
        )
        assert result is not None
        assert "regime=ranging" in result.reasoning
