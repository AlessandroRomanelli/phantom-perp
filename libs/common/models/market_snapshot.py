"""Unified market data model for ETH-PERP."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from libs.common.models.enums import MarketRegime


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Point-in-time market state for ETH-PERP.

    All prices are in USDC. Volumes are in ETH contracts.
    """

    timestamp: datetime
    instrument: str
    mark_price: Decimal
    index_price: Decimal
    last_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    spread_bps: float
    volume_24h: Decimal
    open_interest: Decimal
    funding_rate: Decimal
    next_funding_time: datetime
    hours_since_last_funding: float
    orderbook_imbalance: float
    volatility_1h: float
    volatility_24h: float
    metadata: dict[str, object] = field(default_factory=dict)
    candle_volume_1m: Decimal = Decimal("0")  # Volume of most recent 1-min candle bar
    regime: MarketRegime | None = None  # Populated by signals agent; None until detected

    @property
    def mid_price(self) -> Decimal:
        """Mid-point between best bid and best ask."""
        return (self.best_bid + self.best_ask) / 2

    @property
    def basis_bps(self) -> float:
        """Basis between mark price and index price in basis points."""
        if self.index_price == 0:
            return 0.0
        return float((self.mark_price - self.index_price) / self.index_price * 10000)
