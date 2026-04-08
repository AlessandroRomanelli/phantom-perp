"""Tests for Channel name registry."""

from __future__ import annotations

from libs.common.models.enums import Route
from libs.messaging.channels import Channel


def test_unified_channels() -> None:
    """Unified (pre-routing) channel constants have expected stream names."""
    assert Channel.MARKET_SNAPSHOTS == "stream:market_snapshots"
    assert Channel.SIGNALS == "stream:signals"
    assert Channel.ALERTS == "stream:alerts"
    assert Channel.FUNDING_UPDATES == "stream:funding_updates"
    assert Channel.USER_OVERRIDES == "stream:user_overrides"


def test_route_scoped_channels_a() -> None:
    """Route A scoped channels use ':a' suffix."""
    assert Channel.ranked_ideas(Route.A) == "stream:ranked_ideas:a"
    assert Channel.approved_orders(Route.A) == "stream:approved_orders:a"
    assert Channel.exchange_events(Route.A) == "stream:exchange_events:a"
    assert Channel.portfolio_state(Route.A) == "stream:portfolio_state:a"
    assert Channel.funding_payments(Route.A) == "stream:funding_payments:a"


def test_route_scoped_channels_b() -> None:
    """Route B scoped channels use ':b' suffix."""
    assert Channel.ranked_ideas(Route.B) == "stream:ranked_ideas:b"
    assert Channel.approved_orders(Route.B) == "stream:approved_orders:b"
    assert Channel.exchange_events(Route.B) == "stream:exchange_events:b"
    assert Channel.portfolio_state(Route.B) == "stream:portfolio_state:b"
    assert Channel.funding_payments(Route.B) == "stream:funding_payments:b"


def test_all_channels_count() -> None:
    """all_channels() returns 16 unique channel names."""
    channels = Channel.all_channels()
    assert len(channels) == 16
    assert len(set(channels)) == 16


def test_confirmed_orders_not_scoped() -> None:
    """confirmed_orders() is a unified channel without route suffix."""
    result = Channel.confirmed_orders()
    assert result == "stream:confirmed_orders"
    assert ":a" not in result
    assert ":b" not in result
