"""Resolve conflicting signals using regime-aware weighting.

When the alpha combiner receives both LONG and SHORT signals within a
combination window, this module determines the net direction or cancels
them out if conviction is too close.
"""

from __future__ import annotations

from dataclasses import dataclass

from libs.common.models.enums import MarketRegime, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal

from agents.alpha.scorecard import StrategyScorecard

# Per-regime multipliers: strategies that perform better in each regime
# get a conviction boost; strategies that perform worse get dampened.
_REGIME_BOOSTS: dict[MarketRegime, dict[SignalSource, float]] = {
    MarketRegime.TRENDING_UP: {
        SignalSource.MOMENTUM: 1.3,
        SignalSource.MEAN_REVERSION: 0.7,
    },
    MarketRegime.TRENDING_DOWN: {
        SignalSource.MOMENTUM: 1.3,
        SignalSource.MEAN_REVERSION: 0.7,
    },
    MarketRegime.RANGING: {
        SignalSource.MEAN_REVERSION: 1.3,
        SignalSource.MOMENTUM: 0.7,
        SignalSource.FUNDING_ARB: 1.2,
    },
    MarketRegime.HIGH_VOLATILITY: {
        SignalSource.MOMENTUM: 0.8,
        SignalSource.ORDERBOOK_IMBALANCE: 1.2,
        SignalSource.LIQUIDATION_CASCADE: 1.3,
    },
    MarketRegime.LOW_VOLATILITY: {
        SignalSource.FUNDING_ARB: 1.3,
        SignalSource.MOMENTUM: 0.7,
    },
    MarketRegime.SQUEEZE: {
        SignalSource.MOMENTUM: 1.2,
        SignalSource.ORDERBOOK_IMBALANCE: 1.2,
    },
}

_DEFAULT_NET_CONVICTION_THRESHOLD = 0.15


@dataclass(frozen=True, slots=True)
class ResolvedDirection:
    """Result of conflict resolution."""

    direction: PositionSide
    conviction: float
    sources: list[SignalSource]
    reasoning: str


def resolve_conflicts(
    signals: list[StandardSignal],
    regime: MarketRegime,
    scorecard: StrategyScorecard,
    min_net_conviction: float = _DEFAULT_NET_CONVICTION_THRESHOLD,
) -> ResolvedDirection | None:
    """Resolve a set of potentially conflicting signals.

    Splits signals into LONG and SHORT groups, applies regime-aware and
    scorecard-based weighting, then determines the net direction.

    Args:
        signals: All signals within the combination window.
        regime: Current detected market regime.
        scorecard: Rolling accuracy tracker for strategy weighting.
        min_net_conviction: Minimum net conviction to produce a result.
            If the difference between LONG and SHORT weighted conviction
            falls below this threshold, signals cancel out.

    Returns:
        A ResolvedDirection, or None if signals cancel out.
    """
    if not signals:
        return None

    longs = [s for s in signals if s.direction == PositionSide.LONG]
    shorts = [s for s in signals if s.direction == PositionSide.SHORT]

    # Only one direction present — no conflict to resolve
    if not shorts:
        return _combine_aligned(longs, regime, scorecard)
    if not longs:
        return _combine_aligned(shorts, regime, scorecard)

    # Both directions present: compute weighted conviction per side
    long_score = _weighted_conviction(longs, regime, scorecard)
    short_score = _weighted_conviction(shorts, regime, scorecard)
    net = long_score - short_score

    if abs(net) < min_net_conviction:
        return None  # signals cancel out

    if net > 0:
        winning = longs
        direction = PositionSide.LONG
        losing = shorts
    else:
        winning = shorts
        direction = PositionSide.SHORT
        losing = longs

    sources = list({s.source for s in winning})
    losing_names = [s.source.value for s in losing]

    return ResolvedDirection(
        direction=direction,
        conviction=min(1.0, abs(net)),
        sources=sources,
        reasoning=(
            f"Conflict resolved: {direction.value} "
            f"(net conviction {abs(net):.2f}, "
            f"regime={regime.value}, "
            f"overrode {', '.join(losing_names)})"
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _combine_aligned(
    signals: list[StandardSignal],
    regime: MarketRegime,
    scorecard: StrategyScorecard,
) -> ResolvedDirection:
    """Combine aligned (same-direction) signals into a single result."""
    direction = signals[0].direction

    # Weighted average conviction
    weighted_sum = 0.0
    weight_total = 0.0
    for s in signals:
        w = _signal_weight(s, regime, scorecard)
        weighted_sum += s.conviction * w
        weight_total += w

    base_conviction = weighted_sum / weight_total if weight_total > 0 else 0.0

    # Agreement boost: +0.05 per additional aligned source, capped at +0.20
    agreement_boost = min(0.20, 0.05 * (len(signals) - 1))
    conviction = min(1.0, base_conviction + agreement_boost)

    sources = list({s.source for s in signals})
    source_names = [s.value for s in sources]

    return ResolvedDirection(
        direction=direction,
        conviction=conviction,
        sources=sources,
        reasoning=(
            f"{len(signals)} aligned {direction.value} signals "
            f"({', '.join(source_names)}, "
            f"regime={regime.value}, "
            f"base={base_conviction:.2f}, boost=+{agreement_boost:.2f})"
        ),
    )


def _signal_weight(
    signal: StandardSignal,
    regime: MarketRegime,
    scorecard: StrategyScorecard,
) -> float:
    """Effective weight for a single signal (scorecard * regime boost)."""
    base = scorecard.weight(signal.source)
    regime_boost = _REGIME_BOOSTS.get(regime, {}).get(signal.source, 1.0)
    return base * regime_boost


def _weighted_conviction(
    signals: list[StandardSignal],
    regime: MarketRegime,
    scorecard: StrategyScorecard,
) -> float:
    """Aggregate weighted conviction for a group of same-direction signals."""
    if not signals:
        return 0.0
    total = 0.0
    weight_sum = 0.0
    for s in signals:
        w = _signal_weight(s, regime, scorecard)
        total += s.conviction * w
        weight_sum += w
    return total / weight_sum if weight_sum > 0 else 0.0
