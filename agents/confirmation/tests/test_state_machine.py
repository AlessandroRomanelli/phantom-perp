"""Tests for the order confirmation state machine."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    SignalSource,
)
from libs.common.models.order import ProposedOrder

from agents.confirmation.config import (
    AutoApproveConfig,
    ConfirmationConfig,
    QuietHoursConfig,
)
from agents.confirmation.state_machine import OrderStateMachine

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _order(
    order_id: str = "ord-001",
    conviction: float = 0.7,
    limit_price: Decimal | None = Decimal("2200"),
    size: Decimal = Decimal("1.5"),
    reduce_only: bool = False,
    **overrides: object,
) -> ProposedOrder:
    defaults = dict(
        order_id=order_id,
        signal_id="sig-001",
        instrument="ETH-PERP",
        portfolio_target=PortfolioTarget.B,
        side=OrderSide.BUY,
        size=size,
        order_type=OrderType.LIMIT,
        conviction=conviction,
        sources=[SignalSource.MOMENTUM, SignalSource.SENTIMENT],
        estimated_margin_required_usdc=Decimal("800"),
        estimated_liquidation_price=Decimal("1900"),
        estimated_fee_usdc=Decimal("0.55"),
        estimated_funding_cost_1h_usdc=Decimal("-0.08"),
        proposed_at=T0,
        limit_price=limit_price,
        stop_loss=Decimal("2100"),
        take_profit=Decimal("2400"),
        leverage=Decimal("3"),
        reduce_only=reduce_only,
        status=OrderStatus.RISK_APPROVED,
        reasoning="Breakout detected",
    )
    defaults.update(overrides)
    return ProposedOrder(**defaults)  # type: ignore[arg-type]


def _sm(
    ttl_seconds: float = 300,
    auto_approve_enabled: bool = False,
    auto_approve_max_notional: Decimal = Decimal("2000"),
    auto_approve_min_conviction: float = 0.9,
    auto_approve_only_reduce: bool = False,
    quiet_hours_enabled: bool = False,
) -> OrderStateMachine:
    return OrderStateMachine(
        config=ConfirmationConfig(
            default_ttl=timedelta(seconds=ttl_seconds),
            auto_approve=AutoApproveConfig(
                enabled=auto_approve_enabled,
                max_notional_usdc=auto_approve_max_notional,
                min_conviction=auto_approve_min_conviction,
                only_reduce=auto_approve_only_reduce,
            ),
            quiet_hours=QuietHoursConfig(enabled=quiet_hours_enabled),
        ),
    )


class TestReceiveOrder:
    def test_receive_sets_pending_state(self) -> None:
        sm = _sm()
        pending = sm.receive(_order(), now=T0)
        assert pending.state == OrderStatus.PENDING_CONFIRMATION

    def test_receive_sets_expiry(self) -> None:
        sm = _sm(ttl_seconds=120)
        pending = sm.receive(_order(), now=T0)
        assert pending.expires_at == T0 + timedelta(seconds=120)

    def test_receive_tracks_order(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.receive(_order("ord-2"), now=T0)
        assert len(sm.pending_orders) == 2


class TestApproveOrder:
    def test_approve_returns_approved_order(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        approved = sm.approve("ord-1", now=T0 + timedelta(seconds=30))
        assert approved is not None
        assert approved.order_id == "ord-1"
        assert approved.portfolio_target == PortfolioTarget.B
        assert approved.side == OrderSide.BUY
        assert approved.size == Decimal("1.5")
        assert approved.approved_at == T0 + timedelta(seconds=30)

    def test_approve_sets_confirmed_state(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.approve("ord-1", now=T0)
        pending = sm.get("ord-1")
        assert pending is not None
        assert pending.state == OrderStatus.CONFIRMED

    def test_approve_unknown_order_returns_none(self) -> None:
        sm = _sm()
        assert sm.approve("nonexistent") is None

    def test_approve_already_confirmed_returns_none(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.approve("ord-1", now=T0)
        # Second approve should fail
        assert sm.approve("ord-1") is None

    def test_approve_preserves_prices(self) -> None:
        sm = _sm()
        sm.receive(_order(), now=T0)
        approved = sm.approve("ord-001", now=T0)
        assert approved is not None
        assert approved.limit_price == Decimal("2200")
        assert approved.stop_loss == Decimal("2100")
        assert approved.take_profit == Decimal("2400")
        assert approved.leverage == Decimal("3")


class TestRejectOrder:
    def test_reject_sets_state(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        assert sm.reject("ord-1") is True
        pending = sm.get("ord-1")
        assert pending is not None
        assert pending.state == OrderStatus.REJECTED_BY_USER

    def test_reject_unknown_order_returns_false(self) -> None:
        sm = _sm()
        assert sm.reject("nonexistent") is False

    def test_reject_already_rejected_returns_false(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.reject("ord-1")
        assert sm.reject("ord-1") is False

    def test_rejected_order_removed_from_pending(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.reject("ord-1")
        assert len(sm.pending_orders) == 0


class TestDelay:
    def test_delay_extends_expiry(self) -> None:
        sm = _sm(ttl_seconds=300)
        sm.receive(_order("ord-1"), now=T0)
        sm.delay("ord-1", timedelta(minutes=30), now=T0 + timedelta(seconds=60))
        pending = sm.get("ord-1")
        assert pending is not None
        # Original expiry: T0 + 300s. Delay adds 30 minutes.
        expected = T0 + timedelta(seconds=300) + timedelta(minutes=30)
        assert pending.expires_at == expected

    def test_delay_sets_delay_until(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.delay("ord-1", timedelta(minutes=30), now=T0)
        pending = sm.get("ord-1")
        assert pending is not None
        assert pending.delay_until == T0 + timedelta(minutes=30)

    def test_delay_unknown_order_returns_false(self) -> None:
        sm = _sm()
        assert sm.delay("nonexistent", timedelta(minutes=5)) is False


class TestExpiry:
    def test_expire_stale_marks_expired(self) -> None:
        sm = _sm(ttl_seconds=60)
        sm.receive(_order("ord-1"), now=T0)
        expired = sm.expire_stale(now=T0 + timedelta(seconds=61))
        assert len(expired) == 1
        assert expired[0].state == OrderStatus.EXPIRED
        assert expired[0].order.order_id == "ord-1"

    def test_not_expired_within_ttl(self) -> None:
        sm = _sm(ttl_seconds=60)
        sm.receive(_order("ord-1"), now=T0)
        expired = sm.expire_stale(now=T0 + timedelta(seconds=59))
        assert len(expired) == 0

    def test_already_confirmed_not_expired(self) -> None:
        sm = _sm(ttl_seconds=60)
        sm.receive(_order("ord-1"), now=T0)
        sm.approve("ord-1", now=T0 + timedelta(seconds=10))
        expired = sm.expire_stale(now=T0 + timedelta(seconds=120))
        assert len(expired) == 0

    def test_multiple_orders_expire_independently(self) -> None:
        sm = _sm(ttl_seconds=60)
        sm.receive(_order("ord-1"), now=T0)
        sm.receive(_order("ord-2"), now=T0 + timedelta(seconds=30))
        # At T0+61, ord-1 should expire but ord-2 should not
        expired = sm.expire_stale(now=T0 + timedelta(seconds=61))
        assert len(expired) == 1
        assert expired[0].order.order_id == "ord-1"


class TestAutoApprove:
    def test_auto_approve_disabled(self) -> None:
        sm = _sm(auto_approve_enabled=False)
        order = _order(conviction=0.95, size=Decimal("0.5"), limit_price=Decimal("2000"))
        assert sm.check_auto_approve(order) is False

    def test_auto_approve_meets_all_criteria(self) -> None:
        sm = _sm(
            auto_approve_enabled=True,
            auto_approve_max_notional=Decimal("5000"),
            auto_approve_min_conviction=0.9,
        )
        # notional = 0.5 * 2000 = 1000 USDC, conviction = 0.95
        order = _order(conviction=0.95, size=Decimal("0.5"), limit_price=Decimal("2000"))
        assert sm.check_auto_approve(order) is True

    def test_auto_approve_conviction_too_low(self) -> None:
        sm = _sm(
            auto_approve_enabled=True,
            auto_approve_min_conviction=0.9,
        )
        order = _order(conviction=0.85)
        assert sm.check_auto_approve(order) is False

    def test_auto_approve_notional_too_high(self) -> None:
        sm = _sm(
            auto_approve_enabled=True,
            auto_approve_max_notional=Decimal("2000"),
            auto_approve_min_conviction=0.9,
        )
        # notional = 2 * 2200 = 4400 USDC
        order = _order(conviction=0.95, size=Decimal("2"), limit_price=Decimal("2200"))
        assert sm.check_auto_approve(order) is False

    def test_auto_approve_only_reduce_blocks_non_reduce(self) -> None:
        sm = _sm(
            auto_approve_enabled=True,
            auto_approve_only_reduce=True,
            auto_approve_min_conviction=0.5,
            auto_approve_max_notional=Decimal("100000"),
        )
        order = _order(reduce_only=False, conviction=0.95)
        assert sm.check_auto_approve(order) is False

    def test_auto_approve_only_reduce_allows_reduce(self) -> None:
        sm = _sm(
            auto_approve_enabled=True,
            auto_approve_only_reduce=True,
            auto_approve_min_conviction=0.5,
            auto_approve_max_notional=Decimal("100000"),
        )
        order = _order(reduce_only=True, conviction=0.95, size=Decimal("0.5"), limit_price=Decimal("2000"))
        assert sm.check_auto_approve(order) is True


class TestQuietHours:
    def test_quiet_hours_disabled(self) -> None:
        sm = _sm(quiet_hours_enabled=False)
        assert sm.is_quiet_hours(now=T0) is False

    def test_within_quiet_hours_overnight(self) -> None:
        """23:00–07:00 Europe/Zurich. T0 (12:00 UTC) = 14:00 CEST → not quiet."""
        sm = _sm(quiet_hours_enabled=True)
        # 12:00 UTC = 14:00 CEST (in June, UTC+2)
        assert sm.is_quiet_hours(now=T0) is False

    def test_within_quiet_hours_late_night(self) -> None:
        sm = _sm(quiet_hours_enabled=True)
        # 23:30 UTC = 01:30 CEST (next day) → within 23:00-07:00 local
        late = datetime(2025, 6, 15, 23, 30, 0, tzinfo=UTC)
        assert sm.is_quiet_hours(now=late) is True

    def test_within_quiet_hours_early_morning(self) -> None:
        sm = _sm(quiet_hours_enabled=True)
        # 04:00 UTC = 06:00 CEST → within 23:00-07:00 local
        early = datetime(2025, 6, 15, 4, 0, 0, tzinfo=UTC)
        assert sm.is_quiet_hours(now=early) is True


class TestStalePrice:
    def test_not_stale_within_threshold(self) -> None:
        sm = _sm()  # 1.0% threshold
        assert sm.is_price_stale(Decimal("2200"), Decimal("2210")) is False

    def test_stale_beyond_threshold(self) -> None:
        sm = _sm()  # 1.0% threshold
        # 2200 * 1.01 = 2222 → 2230 is beyond 1%
        assert sm.is_price_stale(Decimal("2200"), Decimal("2230")) is True

    def test_stale_price_down(self) -> None:
        sm = _sm()
        # Drop of more than 1%
        assert sm.is_price_stale(Decimal("2200"), Decimal("2170")) is True

    def test_zero_proposed_price_not_stale(self) -> None:
        sm = _sm()
        assert sm.is_price_stale(Decimal("0"), Decimal("2200")) is False


class TestPurge:
    def test_purge_removes_terminal_orders(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.receive(_order("ord-2"), now=T0)
        sm.approve("ord-1", now=T0)
        sm.reject("ord-2")
        assert sm.purge_terminal() == 2
        assert sm.get("ord-1") is None
        assert sm.get("ord-2") is None

    def test_purge_keeps_pending_orders(self) -> None:
        sm = _sm()
        sm.receive(_order("ord-1"), now=T0)
        sm.receive(_order("ord-2"), now=T0)
        sm.approve("ord-1", now=T0)
        assert sm.purge_terminal() == 1
        assert sm.get("ord-2") is not None
