"""Ranked trade idea — output of the alpha combiner, input to the risk agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PositionSide, Route, SignalSource


@dataclass(frozen=True, slots=True)
class RankedTradeIdea:
    """A trade idea ranked by the alpha combiner and routed to a route.

    Unlike StandardSignal, route is always set (never None) and
    sources may contain multiple contributing signal sources.

    Args:
        idea_id: Unique identifier.
        timestamp: When the idea was created.
        instrument: Instrument identifier (e.g. "ETH-PERP").
        route: Definitive route assignment (A or B).
        direction: LONG or SHORT.
        conviction: Weighted conviction from contributing signals (0–1).
        sources: Signal sources that contributed to this idea.
        time_horizon: Expected holding period.
        entry_price: Suggested entry price (None = market order).
        stop_loss: Suggested stop-loss price.
        take_profit: Suggested take-profit price.
        reasoning: Human-readable explanation.
        metadata: Strategy-specific extras.
    """

    idea_id: str
    timestamp: datetime
    instrument: str
    route: Route
    direction: PositionSide
    conviction: float
    sources: list[SignalSource]
    time_horizon: timedelta
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    reasoning: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError(f"Conviction must be in [0, 1], got {self.conviction}")
        if self.direction == PositionSide.FLAT:
            raise ValueError("Trade direction cannot be FLAT")
