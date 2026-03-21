"""Tests for fee tracking and reporting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import OrderSide, PortfolioTarget
from libs.common.models.order import Fill

from agents.monitoring.fee_report import DualFeeTracker, FeeTracker

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _fill(
    fee: Decimal = Decimal("0.55"),
    is_maker: bool = True,
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("2200"),
    filled_at: datetime = T0,
    order_id: str = "ord-1",
) -> Fill:
    return Fill(
        fill_id=f"fill-{order_id}",
        order_id=order_id,
        portfolio_target=PortfolioTarget.A,
        instrument="ETH-PERP",
        side=OrderSide.BUY,
        size=size,
        price=price,
        fee_usdc=fee,
        is_maker=is_maker,
        filled_at=filled_at,
        trade_id=f"trade-{order_id}",
    )


class TestFeeTracker:
    def test_empty_tracker(self) -> None:
        tracker = FeeTracker(portfolio_target=PortfolioTarget.A)
        summary = tracker.daily_summary()
        assert summary.total_fees_usdc == Decimal("0")
        assert summary.fill_count == 0
        assert summary.maker_ratio == 0.0

    def test_all_maker(self) -> None:
        tracker = FeeTracker(portfolio_target=PortfolioTarget.A)
        tracker.record_fill(_fill(fee=Decimal("0.50"), is_maker=True, order_id="o1"))
        tracker.record_fill(_fill(
            fee=Decimal("0.30"), is_maker=True,
            filled_at=T0 + timedelta(hours=1), order_id="o2",
        ))
        summary = tracker.daily_summary()
        assert summary.total_fees_usdc == Decimal("0.80")
        assert summary.maker_fees_usdc == Decimal("0.80")
        assert summary.taker_fees_usdc == Decimal("0")
        assert summary.maker_ratio == 1.0
        assert summary.maker_count == 2
        assert summary.taker_count == 0

    def test_all_taker(self) -> None:
        tracker = FeeTracker(portfolio_target=PortfolioTarget.A)
        tracker.record_fill(_fill(fee=Decimal("1.00"), is_maker=False))
        summary = tracker.daily_summary()
        assert summary.taker_fees_usdc == Decimal("1.00")
        assert summary.maker_ratio == 0.0

    def test_mixed(self) -> None:
        tracker = FeeTracker(portfolio_target=PortfolioTarget.A)
        tracker.record_fill(_fill(fee=Decimal("0.50"), is_maker=True, order_id="o1"))
        tracker.record_fill(_fill(
            fee=Decimal("1.00"), is_maker=False,
            filled_at=T0 + timedelta(hours=1), order_id="o2",
        ))
        summary = tracker.daily_summary()
        assert summary.total_fees_usdc == Decimal("1.50")
        assert summary.maker_ratio == 0.5

    def test_estimated_savings(self) -> None:
        tracker = FeeTracker(
            portfolio_target=PortfolioTarget.A,
            taker_rate=Decimal("0.000250"),
        )
        # Maker fill: 1 ETH at $2200, fee = $0.275 (maker rate)
        # Hypothetical taker: 1 * 2200 * 0.000250 = $0.55
        # Savings = 0.55 - 0.275 = 0.275
        tracker.record_fill(_fill(
            fee=Decimal("0.275"), is_maker=True,
            size=Decimal("1"), price=Decimal("2200"),
        ))
        summary = tracker.daily_summary()
        assert summary.estimated_savings_usdc == Decimal("0.275")

    def test_old_fills_pruned(self) -> None:
        tracker = FeeTracker(
            portfolio_target=PortfolioTarget.A,
            max_history_hours=24,
        )
        for i in range(48):
            tracker.record_fill(_fill(
                filled_at=T0 + timedelta(hours=i),
                order_id=f"o{i}",
            ))
        assert tracker.fill_count <= 25

    def test_weekly_summary(self) -> None:
        tracker = FeeTracker(portfolio_target=PortfolioTarget.A)
        for i in range(48):
            tracker.record_fill(_fill(
                fee=Decimal("0.50"),
                filled_at=T0 + timedelta(hours=i),
                order_id=f"o{i}",
            ))
        weekly = tracker.weekly_summary()
        assert weekly.total_fees_usdc == Decimal("24.00")
        assert weekly.fill_count == 48


class TestDualFeeTracker:
    def test_independent_tracking(self) -> None:
        dual = DualFeeTracker()
        dual.tracker_a.record_fill(_fill(fee=Decimal("0.50"), order_id="o1"))
        dual.tracker_b.record_fill(_fill(fee=Decimal("1.00"), order_id="o2"))
        assert dual.tracker_a.daily_summary().total_fees_usdc == Decimal("0.50")
        assert dual.tracker_b.daily_summary().total_fees_usdc == Decimal("1.00")
        assert dual.combined_daily_fees_usdc == Decimal("1.50")

    def test_get_tracker(self) -> None:
        dual = DualFeeTracker()
        assert dual.get_tracker(PortfolioTarget.A) is dual.tracker_a
        assert dual.get_tracker(PortfolioTarget.B) is dual.tracker_b
