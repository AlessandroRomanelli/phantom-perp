"""StandardSignal — the universal signal format emitted by all strategies."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import PositionSide, Route, SignalSource


@dataclass(frozen=True, slots=True)
class StandardSignal:
    """A trading signal produced by a strategy.

    This is the contract between the signals agent and the alpha combiner.
    """

    signal_id: str
    timestamp: datetime
    instrument: str
    direction: PositionSide
    conviction: float
    source: SignalSource
    time_horizon: timedelta
    reasoning: str
    suggested_route: Route | None = None
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError(f"Conviction must be in [0, 1], got {self.conviction}")
        if self.direction == PositionSide.FLAT:
            raise ValueError("Signal direction cannot be FLAT — use LONG or SHORT")
