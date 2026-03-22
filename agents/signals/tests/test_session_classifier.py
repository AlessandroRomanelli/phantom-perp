"""Tests for session classifier utility."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.signals.session_classifier import SessionInfo, SessionType, classify_session


def test_saturday_is_crypto_weekend() -> None:
    """Saturday 14:00 UTC should be CRYPTO_WEEKEND."""
    ts = datetime(2026, 3, 21, 14, 0, tzinfo=timezone.utc)  # Saturday
    result = classify_session(ts)
    assert isinstance(result, SessionInfo)
    assert result.session_type == SessionType.CRYPTO_WEEKEND
    assert result.is_weekend is True


def test_sunday_is_crypto_weekend() -> None:
    """Sunday 03:00 UTC should be CRYPTO_WEEKEND."""
    ts = datetime(2026, 3, 22, 3, 0, tzinfo=timezone.utc)  # Sunday
    result = classify_session(ts)
    assert result.session_type == SessionType.CRYPTO_WEEKEND
    assert result.is_weekend is True


def test_monday_equity_hours() -> None:
    """Monday 15:00 UTC (inside 13:30-20:00) should be EQUITY_MARKET_HOURS."""
    ts = datetime(2026, 3, 23, 15, 0, tzinfo=timezone.utc)  # Monday
    result = classify_session(ts)
    assert result.session_type == SessionType.EQUITY_MARKET_HOURS
    assert result.is_equity_hours is True
    assert result.is_weekend is False


def test_tuesday_just_before_equity_open() -> None:
    """Tuesday 13:29 UTC should be CRYPTO_WEEKDAY (just before 13:30)."""
    ts = datetime(2026, 3, 24, 13, 29, tzinfo=timezone.utc)  # Tuesday
    result = classify_session(ts)
    assert result.session_type == SessionType.CRYPTO_WEEKDAY
    assert result.is_equity_hours is False


def test_wednesday_equity_close_boundary() -> None:
    """Wednesday 20:00 UTC is equity close boundary -- should be CRYPTO_WEEKDAY."""
    ts = datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc)  # Wednesday
    result = classify_session(ts)
    assert result.session_type == SessionType.CRYPTO_WEEKDAY
    assert result.is_equity_hours is False


def test_friday_late_not_weekend() -> None:
    """Friday 23:59 UTC should be CRYPTO_WEEKDAY (not weekend yet)."""
    ts = datetime(2026, 3, 20, 23, 59, tzinfo=timezone.utc)  # Friday
    result = classify_session(ts)
    assert result.session_type == SessionType.CRYPTO_WEEKDAY
    assert result.is_weekend is False


def test_monday_midnight_not_weekend() -> None:
    """Monday 00:00 UTC should be CRYPTO_WEEKDAY."""
    ts = datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc)  # Monday
    result = classify_session(ts)
    assert result.session_type == SessionType.CRYPTO_WEEKDAY
    assert result.is_weekend is False


def test_equity_open_boundary_inclusive() -> None:
    """13:30 UTC exactly should be EQUITY_MARKET_HOURS."""
    ts = datetime(2026, 3, 23, 13, 30, tzinfo=timezone.utc)  # Monday 13:30
    result = classify_session(ts)
    assert result.session_type == SessionType.EQUITY_MARKET_HOURS
    assert result.is_equity_hours is True


def test_frozen_dataclass() -> None:
    """SessionInfo should be immutable."""
    ts = datetime(2026, 3, 23, 15, 0, tzinfo=timezone.utc)
    result = classify_session(ts)
    try:
        result.session_type = SessionType.CRYPTO_WEEKEND  # type: ignore[misc]
        raise AssertionError("Should not allow mutation")
    except AttributeError:
        pass
