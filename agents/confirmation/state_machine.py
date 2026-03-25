"""Order confirmation state machine.

Tracks pending orders and enforces state transitions:
    RISK_APPROVED → PENDING_CONFIRMATION
        → CONFIRMED   (user approves)
        → REJECTED    (user rejects)
        → EXPIRED     (TTL exceeded)
        → DELAYED     (user delays, re-enters PENDING after delay)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from libs.common.constants import PORTFOLIO_B_AUTO_APPROVE_MAX_NOTIONAL_USDC
from libs.common.models.enums import OrderStatus, PortfolioTarget
from libs.common.models.order import ApprovedOrder, ProposedOrder
from libs.common.utils import utc_now

from agents.confirmation.config import AutoApproveConfig, ConfirmationConfig, QuietHoursConfig


@dataclass(slots=True)
class PendingOrder:
    """An order awaiting user confirmation."""

    order: ProposedOrder
    received_at: datetime
    expires_at: datetime
    delay_until: datetime | None = None
    state: OrderStatus = OrderStatus.PENDING_CONFIRMATION

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            OrderStatus.CONFIRMED,
            OrderStatus.REJECTED_BY_USER,
            OrderStatus.EXPIRED,
        )


@dataclass
class OrderStateMachine:
    """Manages confirmation state for Portfolio B orders."""

    config: ConfirmationConfig
    _pending: dict[str, PendingOrder] = field(default_factory=dict)
    _max_pending: int = 100

    # -- Ingest ----------------------------------------------------------------

    def receive(self, order: ProposedOrder, now: datetime | None = None) -> PendingOrder:
        """Register a new risk-approved order as pending confirmation.

        Returns the PendingOrder wrapper.
        """
        now = now or utc_now()
        # Auto-purge terminal orders if we're approaching the limit
        if len(self._pending) >= self._max_pending:
            self.purge_terminal()
        # If still at limit after purge, expire oldest non-terminal orders
        if len(self._pending) >= self._max_pending:
            self.expire_stale(now)
            self.purge_terminal()
        pending = PendingOrder(
            order=order,
            received_at=now,
            expires_at=now + self.config.default_ttl,
        )
        self._pending[order.order_id] = pending
        return pending

    # -- User actions ----------------------------------------------------------

    def approve(self, order_id: str, now: datetime | None = None) -> ApprovedOrder | None:
        """User approves the order. Returns ApprovedOrder or None if invalid."""
        now = now or utc_now()
        pending = self._pending.get(order_id)
        if pending is None or pending.is_terminal:
            return None
        pending.state = OrderStatus.CONFIRMED
        return _to_approved(pending.order, now)

    def reject(self, order_id: str) -> bool:
        """User rejects the order. Returns True if the order existed and was pending."""
        pending = self._pending.get(order_id)
        if pending is None or pending.is_terminal:
            return False
        pending.state = OrderStatus.REJECTED_BY_USER
        return True

    def delay(self, order_id: str, delay: timedelta, now: datetime | None = None) -> bool:
        """User delays decision. The order re-enters pending after the delay."""
        now = now or utc_now()
        pending = self._pending.get(order_id)
        if pending is None or pending.is_terminal:
            return False
        pending.delay_until = now + delay
        # Extend expiry by the delay duration
        pending.expires_at = pending.expires_at + delay
        return True

    # -- Expiry ----------------------------------------------------------------

    def expire_stale(self, now: datetime | None = None) -> list[PendingOrder]:
        """Expire orders whose TTL has passed. Returns newly expired orders."""
        now = now or utc_now()
        expired: list[PendingOrder] = []
        for pending in self._pending.values():
            if pending.is_terminal:
                continue
            if now >= pending.expires_at:
                pending.state = OrderStatus.EXPIRED
                expired.append(pending)
        return expired

    # -- Queries ---------------------------------------------------------------

    def get(self, order_id: str) -> PendingOrder | None:
        return self._pending.get(order_id)

    @property
    def pending_orders(self) -> list[PendingOrder]:
        """All orders still awaiting action (not expired/confirmed/rejected)."""
        return [p for p in self._pending.values() if not p.is_terminal]

    @property
    def actionable_orders(self) -> list[PendingOrder]:
        """Pending orders not in a delay window."""
        now = utc_now()
        return [
            p for p in self.pending_orders
            if p.delay_until is None or now >= p.delay_until
        ]

    def purge_terminal(self) -> int:
        """Remove terminal orders from memory. Returns count removed."""
        terminal_ids = [
            oid for oid, p in self._pending.items() if p.is_terminal
        ]
        for oid in terminal_ids:
            del self._pending[oid]
        return len(terminal_ids)

    # -- Auto-approve ----------------------------------------------------------

    def check_auto_approve(self, order: ProposedOrder) -> bool:
        """Check if an order qualifies for auto-approval (bypass Telegram)."""
        aa = self.config.auto_approve
        if not aa.enabled:
            return False
        if aa.only_reduce and not order.reduce_only:
            return False
        if order.conviction < aa.min_conviction:
            return False
        # Enforce the hard-coded constant as a ceiling over any YAML-configured value
        effective_cap = min(aa.max_notional_usdc, PORTFOLIO_B_AUTO_APPROVE_MAX_NOTIONAL_USDC)
        if order.notional_usdc > effective_cap:
            return False
        return True

    # -- Quiet hours -----------------------------------------------------------

    def is_quiet_hours(self, now: datetime | None = None) -> bool:
        """Check if current time falls within quiet hours."""
        qh = self.config.quiet_hours
        if not qh.enabled:
            return False
        now = now or utc_now()
        local_time = now.astimezone(qh.tz).time()
        # Handle overnight range (e.g., 23:00–07:00)
        if qh.start > qh.end:
            return local_time >= qh.start or local_time < qh.end
        return qh.start <= local_time < qh.end

    # -- Stale price -----------------------------------------------------------

    def is_price_stale(
        self,
        proposed_price: Decimal,
        current_price: Decimal,
    ) -> bool:
        """Check if the mark price has moved beyond the stale threshold."""
        if proposed_price <= 0:
            return False
        pct_move = abs(float(current_price - proposed_price)) / float(proposed_price) * 100
        return pct_move > self.config.stale_price_threshold_pct


def _to_approved(order: ProposedOrder, now: datetime) -> ApprovedOrder:
    return ApprovedOrder(
        order_id=order.order_id,
        portfolio_target=order.portfolio_target,
        instrument=order.instrument,
        side=order.side,
        size=order.size,
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        leverage=order.leverage,
        reduce_only=order.reduce_only,
        approved_at=now,
    )
