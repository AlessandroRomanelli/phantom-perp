"""Redis Streams channel name constants and topic registry.

Streams are split by route (*:a, *:b) wherever data is route-specific.
Market data and signals (pre-routing) remain unified.
"""

from __future__ import annotations

from libs.common.models.enums import Route


class Channel:
    """Registry of all Redis Stream channel names.

    Use these constants instead of hardcoding stream names in agents.
    Route-scoped channels have class methods that accept a Route.
    """

    # ── Unified (pre-routing) channels ──────────────────────────────────

    MARKET_SNAPSHOTS = "stream:market_snapshots"
    FUNDING_UPDATES = "stream:funding_updates"
    SIGNALS = "stream:signals"
    ALERTS = "stream:alerts"
    USER_OVERRIDES = "stream:user_overrides"

    # ── Portfolio-scoped channel templates ───────────────────────────────

    _RANKED_IDEAS = "stream:ranked_ideas:{suffix}"
    _APPROVED_ORDERS = "stream:approved_orders:{suffix}"
    _CONFIRMED_ORDERS = "stream:confirmed_orders"
    _EXCHANGE_EVENTS = "stream:exchange_events:{suffix}"
    _PORTFOLIO_STATE = "stream:portfolio_state:{suffix}"
    _FUNDING_PAYMENTS = "stream:funding_payments:{suffix}"

    @classmethod
    def ranked_ideas(cls, target: Route) -> str:
        """Channel for ranked trade ideas routed to a specific route."""
        return cls._RANKED_IDEAS.format(suffix=_target_suffix(target))

    @classmethod
    def approved_orders(cls, target: Route) -> str:
        """Channel for risk-approved orders targeting a specific route.

        Route A: consumed directly by execution.
        Route B: consumed by the confirmation agent.
        """
        return cls._APPROVED_ORDERS.format(suffix=_target_suffix(target))

    @classmethod
    def confirmed_orders(cls) -> str:
        """Channel for user-confirmed orders (Route B only)."""
        return cls._CONFIRMED_ORDERS

    @classmethod
    def exchange_events(cls, target: Route) -> str:
        """Channel for exchange events (fills, order status) per route."""
        return cls._EXCHANGE_EVENTS.format(suffix=_target_suffix(target))

    @classmethod
    def portfolio_state(cls, target: Route) -> str:
        """Channel for portfolio state snapshots."""
        return cls._PORTFOLIO_STATE.format(suffix=_target_suffix(target))

    @classmethod
    def funding_payments(cls, target: Route) -> str:
        """Channel for hourly funding payment events per route."""
        return cls._FUNDING_PAYMENTS.format(suffix=_target_suffix(target))

    @classmethod
    def all_channels(cls) -> list[str]:
        """List all channel names (both unified and route-scoped)."""
        channels = [
            cls.MARKET_SNAPSHOTS,
            cls.FUNDING_UPDATES,
            cls.SIGNALS,
            cls.ALERTS,
            cls.USER_OVERRIDES,
            cls.confirmed_orders(),
        ]
        for target in Route:
            channels.extend([
                cls.ranked_ideas(target),
                cls.approved_orders(target),
                cls.exchange_events(target),
                cls.portfolio_state(target),
                cls.funding_payments(target),
            ])
        return channels


def _target_suffix(target: Route) -> str:
    """Map Route to the stream suffix ('a' or 'b')."""
    return "a" if target == Route.A else "b"
