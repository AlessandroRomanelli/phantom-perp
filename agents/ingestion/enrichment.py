"""Compute derived fields from raw ingestion state.

Calculates spread, orderbook imbalance, and rolling volatility from
candle data to enrich the MarketSnapshot before publishing.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np

from libs.coinbase.models import CandleResponse

from agents.ingestion.state import BookLevel, IngestionState


def compute_spread_bps(best_bid: Decimal, best_ask: Decimal) -> float:
    """Compute the bid-ask spread in basis points.

    Args:
        best_bid: Best bid price.
        best_ask: Best ask price.

    Returns:
        Spread in basis points. Returns 0.0 if mid is zero.
    """
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return 0.0
    return float((best_ask - best_bid) / mid * Decimal("10000"))


def compute_orderbook_imbalance(
    bids: list[BookLevel],
    asks: list[BookLevel],
    depth: int = 10,
) -> float:
    """Compute order book imbalance from L2 depth.

    Imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

    Positive values indicate more buying pressure; negative indicates
    more selling pressure.

    Args:
        bids: Bid levels sorted by price descending.
        asks: Ask levels sorted by price ascending.
        depth: Number of levels to consider per side.

    Returns:
        Imbalance ratio in [-1.0, 1.0]. Returns 0.0 if no depth.
    """
    bid_vol = sum(float(level.size) for level in bids[:depth])
    ask_vol = sum(float(level.size) for level in asks[:depth])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def compute_volatility_from_candles(
    candles: list[CandleResponse],
    periods: int | None = None,
) -> float:
    """Compute annualized realized volatility from candle close prices.

    Uses log returns and annualizes based on the number of periods
    that would fit in a year (assuming hourly candles = 8760 periods/year).

    Args:
        candles: OHLCV candles sorted by timestamp ascending.
        periods: Number of most-recent candles to use. None = all.

    Returns:
        Annualized volatility as a float. Returns 0.0 if insufficient data.
    """
    if len(candles) < 2:
        return 0.0

    closes = [float(c.close) for c in candles]
    if periods is not None:
        closes = closes[-periods:]

    if len(closes) < 2:
        return 0.0

    arr = np.array(closes, dtype=np.float64)
    log_returns = np.diff(np.log(arr))

    if len(log_returns) == 0:
        return 0.0

    std = float(np.std(log_returns, ddof=1))
    # Annualize: assume hourly candles (8760 hours/year)
    return float(std * np.sqrt(8760.0))


def compute_volatility_1h(state: IngestionState) -> float:
    """Compute 1-hour realized volatility from 1-minute candles.

    Uses the last 60 one-minute candles (1 hour of data).
    """
    candles = state.candles_by_granularity.get("ONE_MINUTE", [])
    return compute_volatility_from_candles(candles, periods=60)


def compute_volatility_24h(state: IngestionState) -> float:
    """Compute 24-hour realized volatility from 1-hour candles.

    Uses the last 24 one-hour candles (24 hours of data).
    """
    candles = state.candles_by_granularity.get("ONE_HOUR", [])
    return compute_volatility_from_candles(candles, periods=24)
