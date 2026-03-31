"""Tests for hourly funding payment tracking."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import Route, PositionSide
from libs.common.models.funding import FundingPayment

from agents.reconciliation.funding_tracker import DualFundingTracker, FundingTracker

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _payment(
    rate: Decimal = Decimal("0.0001"),
    payment_usdc: Decimal = Decimal("-0.50"),
    timestamp: datetime = T0,
    side: PositionSide = PositionSide.LONG,
) -> FundingPayment:
    return FundingPayment(
        timestamp=timestamp,
        instrument="ETH-PERP",
        route=Route.A,
        rate=rate,
        payment_usdc=payment_usdc,
        position_size=Decimal("2.5"),
        position_side=side,
        cumulative_24h_usdc=Decimal("0"),
    )


class TestFundingTracker:
    def test_empty_tracker(self) -> None:
        tracker = FundingTracker(route=Route.A)
        assert tracker.cumulative_24h_usdc == Decimal("0")
        assert tracker.payment_count == 0

    def test_record_payment(self) -> None:
        tracker = FundingTracker(route=Route.A)
        tracker.record_payment(_payment(payment_usdc=Decimal("-0.50")))
        assert tracker.cumulative_24h_usdc == Decimal("-0.50")
        assert tracker.payment_count == 1

    def test_cumulative_24h(self) -> None:
        tracker = FundingTracker(route=Route.A)
        for i in range(5):
            tracker.record_payment(_payment(
                payment_usdc=Decimal("-0.10"),
                timestamp=T0 + timedelta(hours=i),
            ))
        assert tracker.cumulative_24h_usdc == Decimal("-0.50")
        assert tracker.payment_count == 5

    def test_old_payments_pruned(self) -> None:
        tracker = FundingTracker(route=Route.A, window_hours=24)
        # Add payments over 30 hours
        for i in range(30):
            tracker.record_payment(_payment(
                payment_usdc=Decimal("-0.10"),
                timestamp=T0 + timedelta(hours=i),
            ))
        # Only last 24 hours should remain
        assert tracker.payment_count <= 25  # 24h window + tolerance
        # Cumulative should only count the windowed payments
        assert tracker.cumulative_24h_usdc > Decimal("-3.00")

    def test_net_positive_when_shorts_receive(self) -> None:
        tracker = FundingTracker(route=Route.A)
        tracker.record_payment(_payment(payment_usdc=Decimal("1.50")))
        assert tracker.net_positive is True

    def test_net_negative_when_longs_pay(self) -> None:
        tracker = FundingTracker(route=Route.A)
        tracker.record_payment(_payment(payment_usdc=Decimal("-1.50")))
        assert tracker.net_positive is False


class TestComputePayment:
    def test_long_pays_positive_rate(self) -> None:
        """When funding rate is positive, longs pay shorts."""
        tracker = FundingTracker(route=Route.A)
        payment = tracker.compute_payment(
            rate=Decimal("0.0001"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            mark_price=Decimal("2000"),
            instrument="ETH-PERP",
            timestamp=T0,
        )
        # payment = -(0.0001 * 2.5 * 2000) = -0.50
        assert payment.payment_usdc == Decimal("-0.50")
        assert tracker.payment_count == 1

    def test_short_receives_positive_rate(self) -> None:
        """When funding rate is positive, shorts receive."""
        tracker = FundingTracker(route=Route.A)
        payment = tracker.compute_payment(
            rate=Decimal("0.0001"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.SHORT,
            mark_price=Decimal("2000"),
            instrument="ETH-PERP",
            timestamp=T0,
        )
        # payment = +(0.0001 * 2.5 * 2000) = +0.50
        assert payment.payment_usdc == Decimal("0.50")

    def test_long_receives_negative_rate(self) -> None:
        """When funding rate is negative, longs receive."""
        tracker = FundingTracker(route=Route.A)
        payment = tracker.compute_payment(
            rate=Decimal("-0.0001"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            mark_price=Decimal("2000"),
            instrument="ETH-PERP",
            timestamp=T0,
        )
        # payment = -(-0.0001 * 2.5 * 2000) = +0.50
        assert payment.payment_usdc == Decimal("0.50")

    def test_cumulative_updated(self) -> None:
        tracker = FundingTracker(route=Route.A)
        tracker.compute_payment(
            rate=Decimal("0.0001"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            mark_price=Decimal("2000"),
            instrument="ETH-PERP",
            timestamp=T0,
        )
        p2 = tracker.compute_payment(
            rate=Decimal("0.0001"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            mark_price=Decimal("2000"),
            instrument="ETH-PERP",
            timestamp=T0 + timedelta(hours=1),
        )
        # Cumulative should be -0.50 + (-0.50) = -1.00
        assert p2.cumulative_24h_usdc == Decimal("-1.00")


class TestDualFundingTracker:
    def test_independent_tracking(self) -> None:
        dual = DualFundingTracker()
        dual.tracker_a.record_payment(_payment(
            payment_usdc=Decimal("-1.00"),
        ))
        dual.tracker_b.record_payment(_payment(
            payment_usdc=Decimal("0.50"),
        ))
        assert dual.tracker_a.cumulative_24h_usdc == Decimal("-1.00")
        assert dual.tracker_b.cumulative_24h_usdc == Decimal("0.50")
        assert dual.combined_24h_usdc == Decimal("-0.50")

    def test_get_tracker(self) -> None:
        dual = DualFundingTracker()
        assert dual.get_tracker(Route.A) is dual.tracker_a
        assert dual.get_tracker(Route.B) is dual.tracker_b
