"""Core enumerations used across the phantom-perp system."""

from enum import Enum


class PortfolioTarget(str, Enum):
    """Target portfolio for trade routing."""

    A = "autonomous"
    B = "user_confirmed"


class PositionSide(str, Enum):
    """Side of a perpetual futures position."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class OrderSide(str, Enum):
    """Side of an order."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type for exchange submission."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    STOP_MARKET = "STOP_MARKET"


class OrderStatus(str, Enum):
    """Lifecycle status of an order."""

    RISK_APPROVED = "risk_approved"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED_BY_USER = "rejected_by_user"
    REJECTED_BY_RISK = "rejected_by_risk"
    EXPIRED = "expired"
    SENT_TO_EXCHANGE = "sent_to_exchange"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED_BY_EXCHANGE = "rejected_by_exchange"


class SignalSource(str, Enum):
    """Origin strategy of a trading signal."""

    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    FUNDING_ARB = "funding_arb"
    CONTRARIAN_FUNDING = "contrarian_funding"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    LIQUIDATION_CASCADE = "liquidation_cascade"
    SENTIMENT = "sentiment"
    CORRELATION = "correlation"
    ONCHAIN = "onchain"
    REGIME_TREND = "regime_trend"
    VWAP = "vwap"
    VOLUME_PROFILE = "volume_profile"
    CLAUDE_MARKET_ANALYSIS = "claude_market_analysis"


class MarketRegime(str, Enum):
    """Detected market regime for regime-aware strategy weighting."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    SQUEEZE = "squeeze"
