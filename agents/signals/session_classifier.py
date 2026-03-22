"""UTC timestamp to session type classification.

Classifies timestamps into trading session types for session-aware
strategy behavior. Crypto markets trade 24/7 but exhibit different
patterns during equity market hours vs weekends.

Follows the established function-based utility pattern from funding_filter.py:
frozen dataclass result, no class state, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SessionType(str, Enum):
    """Trading session classification."""

    CRYPTO_WEEKDAY = "crypto_weekday"
    CRYPTO_WEEKEND = "crypto_weekend"
    EQUITY_MARKET_HOURS = "equity_market_hours"
    EQUITY_OFF_HOURS = "equity_off_hours"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Result of session classification."""

    session_type: SessionType
    is_weekend: bool
    is_equity_hours: bool


def classify_session(ts: datetime) -> SessionInfo:
    """Classify a UTC timestamp into a trading session type.

    Session types:
    - CRYPTO_WEEKEND: Saturday or Sunday (any time)
    - EQUITY_MARKET_HOURS: Mon-Fri 13:30-20:00 UTC (US equity hours)
    - CRYPTO_WEEKDAY: Mon-Fri outside equity hours

    Args:
        ts: UTC datetime to classify.

    Returns:
        SessionInfo with session_type, is_weekend, and is_equity_hours flags.
    """
    weekday = ts.weekday()  # 0=Monday, 5=Saturday, 6=Sunday
    is_weekend = weekday >= 5

    if is_weekend:
        return SessionInfo(
            session_type=SessionType.CRYPTO_WEEKEND,
            is_weekend=True,
            is_equity_hours=False,
        )

    # Check equity market hours: 13:30-20:00 UTC (exclusive of 20:00)
    minutes_since_midnight = ts.hour * 60 + ts.minute
    is_equity_hours = 810 <= minutes_since_midnight < 1200  # 13:30=810, 20:00=1200

    if is_equity_hours:
        return SessionInfo(
            session_type=SessionType.EQUITY_MARKET_HOURS,
            is_weekend=False,
            is_equity_hours=True,
        )

    return SessionInfo(
        session_type=SessionType.CRYPTO_WEEKDAY,
        is_weekend=False,
        is_equity_hours=False,
    )
