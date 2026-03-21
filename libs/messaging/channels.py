"""Redis Streams channel name constants and topic registry.

Streams are split by portfolio (*:a, *:b) wherever data is portfolio-specific.
Market data and signals (pre-routing) remain unified.
"""

from __future__ import annotations

from libs.common.models.enums import PortfolioTarget


class Channel:
    """Registry of all Redis Stream channel names.

    Use these constants instead of hardcoding stream names in agents.
    Portfolio-scoped channels have class methods that accept a PortfolioTarget.
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
    def ranked_ideas(cls, target: PortfolioTarget) -> str:
        """Channel for ranked trade ideas routed to a specific portfolio."""
        return cls._RANKED_IDEAS.format(suffix=_target_suffix(target))

    @classmethod
    def approved_orders(cls, target: PortfolioTarget) -> str:
        """Channel for risk-approved orders targeting a specific portfolio.

        Portfolio A: consumed directly by execution.
        Portfolio B: consumed by the confirmation agent.
        """
        return cls._APPROVED_ORDERS.format(suffix=_target_suffix(target))

    @classmethod
    def confirmed_orders(cls) -> str:
        """Channel for user-confirmed orders (Portfolio B only)."""
        return cls._CONFIRMED_ORDERS

    @classmethod
    def exchange_events(cls, target: PortfolioTarget) -> str:
        """Channel for exchange events (fills, order status) per portfolio."""
        return cls._EXCHANGE_EVENTS.format(suffix=_target_suffix(target))

    @classmethod
    def portfolio_state(cls, target: PortfolioTarget) -> str:
        """Channel for portfolio state snapshots."""
        return cls._PORTFOLIO_STATE.format(suffix=_target_suffix(target))

    @classmethod
    def funding_payments(cls, target: PortfolioTarget) -> str:
        """Channel for hourly funding payment events per portfolio."""
        return cls._FUNDING_PAYMENTS.format(suffix=_target_suffix(target))

    @classmethod
    def all_channels(cls) -> list[str]:
        """List all channel names (both unified and portfolio-scoped)."""
        channels = [
            cls.MARKET_SNAPSHOTS,
            cls.FUNDING_UPDATES,
            cls.SIGNALS,
            cls.ALERTS,
            cls.USER_OVERRIDES,
            cls.confirmed_orders(),
        ]
        for target in PortfolioTarget:
            channels.extend([
                cls.ranked_ideas(target),
                cls.approved_orders(target),
                cls.exchange_events(target),
                cls.portfolio_state(target),
                cls.funding_payments(target),
            ])
        return channels


def _target_suffix(target: PortfolioTarget) -> str:
    """Map PortfolioTarget to the stream suffix ('a' or 'b')."""
    return "a" if target == PortfolioTarget.A else "b"
