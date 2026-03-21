"""Tests for order batching logic."""

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

from agents.confirmation.batching import OrderBatcher

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _order(order_id: str = "ord-001") -> ProposedOrder:
    return ProposedOrder(
        order_id=order_id,
        signal_id="sig-001",
        instrument="ETH-PERP",
        portfolio_target=PortfolioTarget.B,
        side=OrderSide.BUY,
        size=Decimal("1"),
        order_type=OrderType.LIMIT,
        conviction=0.7,
        sources=[SignalSource.MOMENTUM],
        estimated_margin_required_usdc=Decimal("500"),
        estimated_liquidation_price=Decimal("1800"),
        estimated_fee_usdc=Decimal("0.50"),
        estimated_funding_cost_1h_usdc=Decimal("0.05"),
        proposed_at=T0,
        limit_price=Decimal("2200"),
        stop_loss=Decimal("2100"),
        take_profit=Decimal("2400"),
        leverage=Decimal("3"),
        status=OrderStatus.RISK_APPROVED,
    )


class TestOrderBatcher:
    def test_first_order_buffered(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=30), max_batch_size=5)
        result = batcher.add(_order("o1"), now=T0)
        assert result is None
        assert batcher.buffered_count == 1

    def test_window_flush(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=10), max_batch_size=5)
        batcher.add(_order("o1"), now=T0)
        batcher.add(_order("o2"), now=T0 + timedelta(seconds=5))
        # Window elapses: next add triggers flush
        result = batcher.add(_order("o3"), now=T0 + timedelta(seconds=11))
        assert result is not None
        assert len(result) == 2  # o1 and o2 flushed
        assert result[0].order_id == "o1"
        assert result[1].order_id == "o2"
        # o3 starts a new window
        assert batcher.buffered_count == 1

    def test_max_batch_size_flush(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=60), max_batch_size=3)
        batcher.add(_order("o1"), now=T0)
        batcher.add(_order("o2"), now=T0 + timedelta(seconds=1))
        result = batcher.add(_order("o3"), now=T0 + timedelta(seconds=2))
        assert result is not None
        assert len(result) == 3

    def test_manual_flush(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=30), max_batch_size=5)
        batcher.add(_order("o1"), now=T0)
        batcher.add(_order("o2"), now=T0 + timedelta(seconds=5))
        result = batcher.flush()
        assert result is not None
        assert len(result) == 2
        assert batcher.is_empty

    def test_flush_empty_returns_none(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=30), max_batch_size=5)
        assert batcher.flush() is None

    def test_single_order_flush(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=5), max_batch_size=5)
        batcher.add(_order("o1"), now=T0)
        # Window elapses
        result = batcher.add(_order("o2"), now=T0 + timedelta(seconds=10))
        assert result is not None
        assert len(result) == 1
        assert result[0].order_id == "o1"

    def test_successive_batches(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=5), max_batch_size=5)
        batcher.add(_order("o1"), now=T0)
        # First window flush
        batch1 = batcher.add(_order("o2"), now=T0 + timedelta(seconds=6))
        assert batch1 is not None
        assert len(batch1) == 1
        # Second window
        batcher.add(_order("o3"), now=T0 + timedelta(seconds=7))
        batch2 = batcher.add(_order("o4"), now=T0 + timedelta(seconds=12))
        assert batch2 is not None
        assert len(batch2) == 2  # o2 and o3

    def test_is_empty(self) -> None:
        batcher = OrderBatcher(window=timedelta(seconds=30), max_batch_size=5)
        assert batcher.is_empty is True
        batcher.add(_order("o1"), now=T0)
        assert batcher.is_empty is False
        batcher.flush()
        assert batcher.is_empty is True
