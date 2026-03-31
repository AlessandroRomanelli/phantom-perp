"""Tests for Redis Streams channel name registry."""

from libs.common.models.enums import Route
from libs.messaging.channels import Channel


class TestChannelNames:
    def test_unified_channels_are_strings(self) -> None:
        assert isinstance(Channel.MARKET_SNAPSHOTS, str)
        assert isinstance(Channel.FUNDING_UPDATES, str)
        assert isinstance(Channel.SIGNALS, str)
        assert isinstance(Channel.ALERTS, str)

    def test_portfolio_scoped_channels_differ(self) -> None:
        assert Channel.ranked_ideas(Route.A) != Channel.ranked_ideas(Route.B)
        assert Channel.approved_orders(Route.A) != Channel.approved_orders(
            Route.B
        )
        assert Channel.exchange_events(Route.A) != Channel.exchange_events(
            Route.B
        )

    def test_route_a_suffix(self) -> None:
        assert Channel.ranked_ideas(Route.A).endswith(":a")
        assert Channel.approved_orders(Route.A).endswith(":a")
        assert Channel.portfolio_state(Route.A).endswith(":a")
        assert Channel.funding_payments(Route.A).endswith(":a")

    def test_route_b_suffix(self) -> None:
        assert Channel.ranked_ideas(Route.B).endswith(":b")
        assert Channel.approved_orders(Route.B).endswith(":b")

    def test_all_channels_returns_complete_list(self) -> None:
        all_ch = Channel.all_channels()
        assert Channel.MARKET_SNAPSHOTS in all_ch
        assert Channel.SIGNALS in all_ch
        assert Channel.ranked_ideas(Route.A) in all_ch
        assert Channel.ranked_ideas(Route.B) in all_ch
        assert Channel.approved_orders(Route.A) in all_ch
        assert Channel.approved_orders(Route.B) in all_ch
        # At least: 5 unified + 2*5 portfolio-scoped + 1 confirmed_orders = 16
        assert len(all_ch) >= 16

    def test_no_duplicate_channels(self) -> None:
        all_ch = Channel.all_channels()
        assert len(all_ch) == len(set(all_ch))
