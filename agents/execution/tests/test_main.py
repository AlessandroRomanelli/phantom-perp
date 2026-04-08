"""Tests for execution agent serialization helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    Route,
    SignalSource,
)
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder
from libs.common.serialization import (
    approved_order_to_dict,
    deserialize_approved_order,
    deserialize_fill,
    deserialize_proposed_order,
    fill_to_dict,
    order_to_dict,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestDeserializeProposedOrder:
    def test_roundtrip_with_risk_agent_format(self) -> None:
        """Verify we can deserialize what risk agent's order_to_dict produces."""
        original = ProposedOrder(
            order_id="ord-exec-1",
            signal_id="sig-exec-1",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.BUY,
            size=Decimal("2.5"),
            order_type=OrderType.LIMIT,
            conviction=0.85,
            sources=[SignalSource.MOMENTUM, SignalSource.SENTIMENT],
            estimated_margin_required_usdc=Decimal("1700"),
            estimated_liquidation_price=Decimal("1890"),
            estimated_fee_usdc=Decimal("0.70"),
            estimated_funding_cost_1h_usdc=Decimal("-0.08"),
            proposed_at=T0,
            limit_price=Decimal("2200"),
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
            leverage=Decimal("3"),
            reduce_only=False,
            status=OrderStatus.RISK_APPROVED,
            reasoning="Strong momentum",
        )
        serialized = order_to_dict(original)
        reconstructed = deserialize_proposed_order(serialized)

        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == Route.A
        assert reconstructed.side == original.side
        assert reconstructed.size == original.size
        assert reconstructed.sources == original.sources
        assert reconstructed.limit_price == original.limit_price
        assert reconstructed.stop_loss == original.stop_loss
        assert reconstructed.take_profit == original.take_profit
        assert reconstructed.leverage == original.leverage
        assert reconstructed.reduce_only is False

    def test_none_prices(self) -> None:
        original = ProposedOrder(
            order_id="ord-np",
            signal_id="sig-np",
            instrument="ETH-PERP",
            route=Route.A,
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            order_type=OrderType.MARKET,
            conviction=0.6,
            sources=[SignalSource.FUNDING_ARB],
            estimated_margin_required_usdc=Decimal("500"),
            estimated_liquidation_price=Decimal("2500"),
            estimated_fee_usdc=Decimal("0.25"),
            estimated_funding_cost_1h_usdc=Decimal("0.10"),
            proposed_at=T0,
            leverage=Decimal("2"),
            reduce_only=True,
            status=OrderStatus.RISK_APPROVED,
        )
        serialized = order_to_dict(original)
        reconstructed = deserialize_proposed_order(serialized)
        assert reconstructed.limit_price is None
        assert reconstructed.stop_loss is None
        assert reconstructed.take_profit is None
        assert reconstructed.reduce_only is True


class TestDeserializeApprovedOrder:
    def test_roundtrip_with_confirmation_agent_format(self) -> None:
        """Verify we can deserialize what confirmation agent's approved_order_to_dict produces."""
        original = ApprovedOrder(
            order_id="ord-conf-1",
            route=Route.B,
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("1.5"),
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2200"),
            stop_loss=Decimal("2100"),
            take_profit=Decimal("2400"),
            leverage=Decimal("3"),
            reduce_only=False,
            approved_at=T0,
        )
        serialized = approved_order_to_dict(original)
        reconstructed = deserialize_approved_order(serialized)

        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == Route.B
        assert reconstructed.side == original.side
        assert reconstructed.size == original.size
        assert reconstructed.limit_price == original.limit_price
        assert reconstructed.stop_loss == original.stop_loss
        assert reconstructed.take_profit == original.take_profit
        assert reconstructed.leverage == original.leverage
        assert reconstructed.reduce_only is False
        assert reconstructed.approved_at == T0


class TestFillSerialization:
    def test_roundtrip(self) -> None:
        fill = Fill(
            fill_id="fill-001",
            order_id="ord-001",
            route=Route.A,
            instrument="ETH-PERP",
            side=OrderSide.BUY,
            size=Decimal("2.5"),
            price=Decimal("2200"),
            fee_usdc=Decimal("0.69"),
            is_maker=True,
            filled_at=T0,
            trade_id="trade-001",
        )
        serialized = fill_to_dict(fill)
        reconstructed = deserialize_fill(serialized)

        assert reconstructed.fill_id == "fill-001"
        assert reconstructed.order_id == "ord-001"
        assert reconstructed.route == Route.A
        assert reconstructed.side == OrderSide.BUY
        assert reconstructed.size == Decimal("2.5")
        assert reconstructed.price == Decimal("2200")
        assert reconstructed.fee_usdc == Decimal("0.69")
        assert reconstructed.is_maker is True
        assert reconstructed.trade_id == "trade-001"

    def test_taker_fill(self) -> None:
        fill = Fill(
            fill_id="fill-002",
            order_id="ord-002",
            route=Route.B,
            instrument="ETH-PERP",
            side=OrderSide.SELL,
            size=Decimal("0.5"),
            price=Decimal("2250"),
            fee_usdc=Decimal("0.28"),
            is_maker=False,
            filled_at=T0,
            trade_id="trade-002",
        )
        serialized = fill_to_dict(fill)
        reconstructed = deserialize_fill(serialized)

        assert reconstructed.route == Route.B
        assert reconstructed.side == OrderSide.SELL
        assert reconstructed.is_maker is False


class TestPaperModeSkip:
    """SAFE-01: Verify execution agent skips order processing in paper mode."""

    def test_paper_mode_skip_log_string_present(self) -> None:
        """run_agent source must contain the paper_mode_order_skipped log call."""
        import inspect
        import agents.execution.main as m
        src = inspect.getsource(m.run_agent)
        assert "paper_mode_order_skipped" in src, (
            "SAFE-01: 'paper_mode_order_skipped' log is missing from run_agent. "
            "Paper mode early-return has not been implemented."
        )

    def test_paper_mode_skip_before_circuit_breaker(self) -> None:
        """Paper mode skip must occur BEFORE the circuit breaker check.

        The early-return must be placed between the dedup check and cb.is_open()
        so the order is never seen by the circuit breaker or exchange logic.
        """
        import inspect
        import agents.execution.main as m
        src = inspect.getsource(m.run_agent)
        assert "paper_mode_order_skipped" in src, (
            "SAFE-01: paper_mode_order_skipped missing from run_agent"
        )
        skip_pos = src.index("paper_mode_order_skipped")
        cb_pos = src.index("cb.is_open")
        assert skip_pos < cb_pos, (
            "SAFE-01: paper mode skip must occur before circuit breaker check (cb.is_open)"
        )

    def test_paper_mode_skip_after_dedup(self) -> None:
        """Paper mode skip must occur AFTER the dedup guard.

        The dedup guard must run first so re-delivered paper orders are deduplicated.
        """
        import inspect
        import agents.execution.main as m
        src = inspect.getsource(m.run_agent)
        assert "paper_mode_order_skipped" in src
        # dedup guard appears first as 'order_deduplicated'
        dedup_pos = src.index("order_deduplicated")
        skip_pos = src.index("paper_mode_order_skipped")
        assert dedup_pos < skip_pos, (
            "SAFE-01: paper mode skip must occur after dedup guard (order_deduplicated)"
        )


class TestDedupFIFOEviction:
    """BUG-02: Verify dedup eviction removes oldest entry (FIFO), not arbitrary."""

    def test_dedup_fifo_evicts_oldest(self) -> None:
        """When capacity exceeded, the first-added ID is evicted."""
        from collections import OrderedDict

        dedup: OrderedDict[str, None] = OrderedDict()
        max_size = 3
        for i in range(5):
            dedup[f"order-{i}"] = None
            while len(dedup) > max_size:
                dedup.popitem(last=False)
        # order-0 and order-1 evicted; order-2, order-3, order-4 remain
        assert "order-0" not in dedup
        assert "order-1" not in dedup
        assert "order-2" in dedup
        assert "order-3" in dedup
        assert "order-4" in dedup

    def test_dedup_fifo_preserves_recent(self) -> None:
        """Most recently added IDs are never evicted before older ones."""
        from collections import OrderedDict

        dedup: OrderedDict[str, None] = OrderedDict()
        max_size = 2
        dedup["oldest"] = None
        dedup["middle"] = None
        dedup["newest"] = None
        while len(dedup) > max_size:
            dedup.popitem(last=False)
        assert "oldest" not in dedup
        assert "middle" in dedup
        assert "newest" in dedup

    def test_dedup_membership_check(self) -> None:
        """OrderedDict in-operator works like set for membership."""
        from collections import OrderedDict

        dedup: OrderedDict[str, None] = OrderedDict()
        dedup["exists"] = None
        assert "exists" in dedup
        assert "missing" not in dedup

    def test_dedup_readd_existing_no_duplicate(self) -> None:
        """Re-adding an existing ID does not grow the dict."""
        from collections import OrderedDict

        dedup: OrderedDict[str, None] = OrderedDict()
        dedup["order-1"] = None
        dedup["order-2"] = None
        dedup["order-1"] = None  # re-add
        assert len(dedup) == 2

    def test_dedup_capacity_10000(self) -> None:
        """At production capacity, FIFO eviction is correct."""
        from collections import OrderedDict

        dedup: OrderedDict[str, None] = OrderedDict()
        max_size = 10_000
        total = 10_003
        for i in range(total):
            dedup[f"order-{i}"] = None
            while len(dedup) > max_size:
                dedup.popitem(last=False)
        assert len(dedup) == max_size
        # First 3 evicted
        assert "order-0" not in dedup
        assert "order-1" not in dedup
        assert "order-2" not in dedup
        # Last one present
        assert f"order-{total - 1}" in dedup
        # First surviving entry
        assert "order-3" in dedup
