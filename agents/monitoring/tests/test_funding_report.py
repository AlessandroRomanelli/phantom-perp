"""Tests for funding payment aggregation and reporting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import Route, PositionSide
from libs.common.models.funding import FundingPayment

from agents.monitoring.funding_report import (
    DualFundingReporter,
    FundingReporter,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _payment(
    payment_usdc: Decimal = Decimal("-0.50"),
    rate: Decimal = Decimal("0.0001"),
    timestamp: datetime = T0,
) -> FundingPayment:
    return FundingPayment(
        timestamp=timestamp,
        instrument="ETH-PERP",
        route=Route.A,
        rate=rate,
        payment_usdc=payment_usdc,
        position_size=Decimal("2.5"),
        position_side=PositionSide.LONG,
        cumulative_24h_usdc=Decimal("0"),
    )


class TestFundingReporter:
    def test_empty_reporter(self) -> None:
        reporter = FundingReporter(route=Route.A)
        summary = reporter.daily_summary()
        assert summary.total_usdc == Decimal("0")
        assert summary.payment_count == 0

    def test_record_and_daily_summary(self) -> None:
        reporter = FundingReporter(route=Route.A)
        for i in range(5):
            reporter.record_payment(_payment(
                payment_usdc=Decimal("-0.10"),
                timestamp=T0 + timedelta(hours=i),
            ))
        summary = reporter.daily_summary()
        assert summary.total_usdc == Decimal("-0.50")
        assert summary.payment_count == 5
        assert summary.window_label == "24h"

    def test_hourly_summary_filters(self) -> None:
        reporter = FundingReporter(route=Route.A)
        # Add payment 2 hours ago and 30 minutes ago
        reporter.record_payment(_payment(timestamp=T0))
        reporter.record_payment(_payment(timestamp=T0 + timedelta(hours=2)))
        hourly = reporter.hourly_summary()
        # Only the latest (within 1h of most recent) should appear
        assert hourly.payment_count == 1

    def test_weekly_summary(self) -> None:
        reporter = FundingReporter(route=Route.A)
        # Add payments over 5 days
        for i in range(120):  # 5 days worth of hourly
            reporter.record_payment(_payment(
                payment_usdc=Decimal("-0.10"),
                timestamp=T0 + timedelta(hours=i),
            ))
        weekly = reporter.weekly_summary()
        assert weekly.payment_count == 120
        assert weekly.total_usdc == Decimal("-12.00")

    def test_old_payments_pruned(self) -> None:
        reporter = FundingReporter(
            route=Route.A,
            max_history_hours=24,
        )
        for i in range(48):
            reporter.record_payment(_payment(
                timestamp=T0 + timedelta(hours=i),
            ))
        # Only last 24 hours should remain
        assert reporter.payment_count <= 25

    def test_avg_rate(self) -> None:
        reporter = FundingReporter(route=Route.A)
        reporter.record_payment(_payment(rate=Decimal("0.0001"), timestamp=T0))
        reporter.record_payment(_payment(
            rate=Decimal("0.0003"),
            timestamp=T0 + timedelta(hours=1),
        ))
        summary = reporter.daily_summary()
        assert summary.avg_rate == Decimal("0.0002")

    def test_min_max_payment(self) -> None:
        reporter = FundingReporter(route=Route.A)
        reporter.record_payment(_payment(payment_usdc=Decimal("-1.00"), timestamp=T0))
        reporter.record_payment(_payment(
            payment_usdc=Decimal("0.50"),
            timestamp=T0 + timedelta(hours=1),
        ))
        summary = reporter.daily_summary()
        assert summary.min_payment_usdc == Decimal("-1.00")
        assert summary.max_payment_usdc == Decimal("0.50")

    def test_net_positive(self) -> None:
        reporter = FundingReporter(route=Route.A)
        reporter.record_payment(_payment(payment_usdc=Decimal("2.00"), timestamp=T0))
        reporter.record_payment(_payment(
            payment_usdc=Decimal("-0.50"),
            timestamp=T0 + timedelta(hours=1),
        ))
        summary = reporter.daily_summary()
        assert summary.net_positive is True

    def test_entries(self) -> None:
        reporter = FundingReporter(route=Route.A)
        reporter.record_payment(_payment(timestamp=T0))
        reporter.record_payment(_payment(timestamp=T0 + timedelta(hours=1)))
        entries = reporter.entries(hours=24)
        assert len(entries) == 2
        assert entries[0].timestamp == T0


class TestDualFundingReporter:
    def test_independent_tracking(self) -> None:
        dual = DualFundingReporter()
        dual.reporter_a.record_payment(_payment(
            payment_usdc=Decimal("-1.00"), timestamp=T0,
        ))
        dual.reporter_b.record_payment(_payment(
            payment_usdc=Decimal("0.50"), timestamp=T0,
        ))
        assert dual.reporter_a.daily_summary().total_usdc == Decimal("-1.00")
        assert dual.reporter_b.daily_summary().total_usdc == Decimal("0.50")
        assert dual.combined_daily_usdc == Decimal("-0.50")

    def test_get_reporter(self) -> None:
        dual = DualFundingReporter()
        assert dual.get_reporter(Route.A) is dual.reporter_a
        assert dual.get_reporter(Route.B) is dual.reporter_b
