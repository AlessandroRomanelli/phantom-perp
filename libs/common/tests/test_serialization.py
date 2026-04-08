"""Unit tests for libs.common.serialization — round-trip and edge case coverage."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, UTC
from decimal import Decimal

from libs.common.models.enums import (
    MarketRegime,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    Route,
    SignalSource,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.order import ApprovedOrder, Fill, ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.serialization import (
    _parse_bool,
    approved_order_to_dict,
    deserialize_approved_order,
    deserialize_fill,
    deserialize_funding_payment,
    deserialize_idea,
    deserialize_portfolio_snapshot,
    deserialize_proposed_order,
    deserialize_signal,
    deserialize_snapshot,
    fill_to_dict,
    funding_payment_to_dict,
    idea_to_dict,
    order_to_dict,
    portfolio_snapshot_to_dict,
    signal_to_dict,
    snapshot_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------

T0 = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2024, 1, 15, 13, 0, 0, tzinfo=UTC)


def _make_proposed_order(
    reduce_only: bool = False,
    limit_price: Decimal | None = None,
    stop_loss: Decimal | None = None,
    take_profit: Decimal | None = None,
) -> ProposedOrder:
    return ProposedOrder(
        order_id="ord-001",
        signal_id="sig-001",
        instrument="ETH-PERP",
        route=Route.A,
        side=OrderSide.BUY,
        size=Decimal("1.5"),
        order_type=OrderType.MARKET,
        conviction=0.85,
        sources=[SignalSource.MOMENTUM, SignalSource.VWAP],
        estimated_margin_required_usdc=Decimal("500.00"),
        estimated_liquidation_price=Decimal("1800.00"),
        estimated_fee_usdc=Decimal("1.25"),
        estimated_funding_cost_1h_usdc=Decimal("0.10"),
        proposed_at=T0,
        limit_price=limit_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        leverage=Decimal("3"),
        reduce_only=reduce_only,
        status=OrderStatus.RISK_APPROVED,
        reasoning="Strong momentum signal",
    )


def _make_approved_order(reduce_only: bool = False) -> ApprovedOrder:
    return ApprovedOrder(
        order_id="ord-002",
        route=Route.B,
        instrument="BTC-PERP",
        side=OrderSide.SELL,
        size=Decimal("0.1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("42000.00"),
        stop_loss=Decimal("43000.00"),
        take_profit=Decimal("40000.00"),
        leverage=Decimal("2"),
        reduce_only=reduce_only,
        approved_at=T1,
    )


def _make_fill(is_maker: bool = False) -> Fill:
    return Fill(
        fill_id="fill-001",
        order_id="ord-001",
        route=Route.A,
        instrument="ETH-PERP",
        side=OrderSide.BUY,
        size=Decimal("1.5"),
        price=Decimal("2300.00"),
        fee_usdc=Decimal("1.25"),
        is_maker=is_maker,
        filled_at=T0,
        trade_id="trade-001",
    )


def _make_trade_idea() -> RankedTradeIdea:
    return RankedTradeIdea(
        idea_id="idea-001",
        timestamp=T0,
        instrument="SOL-PERP",
        route=Route.A,
        direction=PositionSide.LONG,
        conviction=0.75,
        sources=[SignalSource.MOMENTUM],
        time_horizon=timedelta(hours=2),
        entry_price=Decimal("95.50"),
        stop_loss=Decimal("90.00"),
        take_profit=Decimal("110.00"),
        reasoning="Bullish momentum",
    )


def _make_market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=T0,
        instrument="ETH-PERP",
        mark_price=Decimal("2300.00"),
        index_price=Decimal("2298.50"),
        last_price=Decimal("2299.75"),
        best_bid=Decimal("2299.50"),
        best_ask=Decimal("2300.50"),
        spread_bps=4.35,
        volume_24h=Decimal("12500.00"),
        open_interest=Decimal("45000.00"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=T1,
        hours_since_last_funding=1.5,
        orderbook_imbalance=0.3,
        volatility_1h=0.02,
        volatility_24h=0.05,
    )


def _make_standard_signal(
    suggested_route: Route | None = Route.A,
) -> StandardSignal:
    return StandardSignal(
        signal_id="sig-001",
        timestamp=T0,
        instrument="ETH-PERP",
        direction=PositionSide.LONG,
        conviction=0.8,
        source=SignalSource.MOMENTUM,
        time_horizon=timedelta(hours=1),
        reasoning="Test signal",
        suggested_route=suggested_route,
        entry_price=Decimal("2300.00"),
        stop_loss=Decimal("2200.00"),
        take_profit=Decimal("2500.00"),
        metadata={"extra": "value"},
    )


def _make_portfolio_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=T0,
        route=Route.A,
        equity_usdc=Decimal("10000.00"),
        used_margin_usdc=Decimal("2000.00"),
        available_margin_usdc=Decimal("8000.00"),
        margin_utilization_pct=0.2,
        positions=[],
        unrealized_pnl_usdc=Decimal("150.00"),
        realized_pnl_today_usdc=Decimal("75.00"),
        funding_pnl_today_usdc=Decimal("-5.00"),
        fees_paid_today_usdc=Decimal("12.50"),
    )


def _make_funding_payment() -> FundingPayment:
    return FundingPayment(
        timestamp=T0,
        instrument="ETH-PERP",
        route=Route.A,
        rate=Decimal("0.0001"),
        payment_usdc=Decimal("-2.50"),
        position_size=Decimal("5.0"),
        position_side=PositionSide.LONG,
        cumulative_24h_usdc=Decimal("-10.00"),
    )


# ---------------------------------------------------------------------------
# TestParseBool
# ---------------------------------------------------------------------------


class TestParseBool:
    def test_true_bool(self) -> None:
        assert _parse_bool(True) is True

    def test_false_bool(self) -> None:
        assert _parse_bool(False) is False

    def test_string_True(self) -> None:
        assert _parse_bool("True") is True

    def test_string_False(self) -> None:
        assert _parse_bool("False") is False

    def test_string_true_lowercase(self) -> None:
        assert _parse_bool("true") is True

    def test_string_false_lowercase(self) -> None:
        assert _parse_bool("false") is False

    def test_string_1(self) -> None:
        assert _parse_bool("1") is True

    def test_string_0(self) -> None:
        assert _parse_bool("0") is False

    def test_int_1(self) -> None:
        assert _parse_bool(1) is True

    def test_int_0(self) -> None:
        assert _parse_bool(0) is False

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_bool("invalid")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_bool("")


# ---------------------------------------------------------------------------
# TestDeserializeProposedOrder
# ---------------------------------------------------------------------------


class TestDeserializeProposedOrder:
    def test_round_trip(self) -> None:
        """order_to_dict -> deserialize_proposed_order produces equivalent object."""
        original = _make_proposed_order()
        payload = order_to_dict(original)
        result = deserialize_proposed_order(payload)
        assert result.order_id == original.order_id
        assert result.signal_id == original.signal_id
        assert result.instrument == original.instrument
        assert result.route == original.route
        assert result.side == original.side
        assert result.size == original.size
        assert result.order_type == original.order_type
        assert result.conviction == original.conviction
        assert result.sources == original.sources
        assert result.estimated_margin_required_usdc == original.estimated_margin_required_usdc
        assert result.estimated_liquidation_price == original.estimated_liquidation_price
        assert result.estimated_fee_usdc == original.estimated_fee_usdc
        assert result.estimated_funding_cost_1h_usdc == original.estimated_funding_cost_1h_usdc
        assert result.proposed_at == original.proposed_at
        assert result.leverage == original.leverage
        assert result.reduce_only == original.reduce_only
        assert result.status == original.status
        assert result.reasoning == original.reasoning

    def test_reduce_only_string_True(self) -> None:
        order = _make_proposed_order(reduce_only=True)
        payload = order_to_dict(order)
        # Simulate the Redis str representation
        payload["reduce_only"] = "True"
        result = deserialize_proposed_order(payload)
        assert result.reduce_only is True

    def test_reduce_only_string_true_lowercase(self) -> None:
        order = _make_proposed_order(reduce_only=True)
        payload = order_to_dict(order)
        payload["reduce_only"] = "true"
        result = deserialize_proposed_order(payload)
        assert result.reduce_only is True

    def test_optional_fields_empty_string_to_none(self) -> None:
        order = _make_proposed_order()
        payload = order_to_dict(order)
        # Ensure no limit_price/stop_loss/take_profit set -> empty string in payload
        assert payload["limit_price"] == ""
        assert payload["stop_loss"] == ""
        assert payload["take_profit"] == ""
        result = deserialize_proposed_order(payload)
        assert result.limit_price is None
        assert result.stop_loss is None
        assert result.take_profit is None

    def test_optional_fields_with_values(self) -> None:
        order = _make_proposed_order(
            limit_price=Decimal("2300.00"),
            stop_loss=Decimal("2200.00"),
            take_profit=Decimal("2500.00"),
        )
        payload = order_to_dict(order)
        result = deserialize_proposed_order(payload)
        assert result.limit_price == Decimal("2300.00")
        assert result.stop_loss == Decimal("2200.00")
        assert result.take_profit == Decimal("2500.00")


# ---------------------------------------------------------------------------
# TestDeserializeApprovedOrder
# ---------------------------------------------------------------------------


class TestDeserializeApprovedOrder:
    def test_round_trip(self) -> None:
        """approved_order_to_dict -> deserialize_approved_order produces equivalent."""
        original = _make_approved_order()
        payload = approved_order_to_dict(original)
        result = deserialize_approved_order(payload)
        assert result.order_id == original.order_id
        assert result.route == original.route
        assert result.instrument == original.instrument
        assert result.side == original.side
        assert result.size == original.size
        assert result.order_type == original.order_type
        assert result.limit_price == original.limit_price
        assert result.stop_loss == original.stop_loss
        assert result.take_profit == original.take_profit
        assert result.leverage == original.leverage
        assert result.reduce_only == original.reduce_only
        assert result.approved_at == original.approved_at

    def test_reduce_only_string_True(self) -> None:
        order = _make_approved_order(reduce_only=True)
        payload = approved_order_to_dict(order)
        payload["reduce_only"] = "True"
        result = deserialize_approved_order(payload)
        assert result.reduce_only is True

    def test_reduce_only_false_round_trip(self) -> None:
        order = _make_approved_order(reduce_only=False)
        payload = approved_order_to_dict(order)
        result = deserialize_approved_order(payload)
        assert result.reduce_only is False


# ---------------------------------------------------------------------------
# TestDeserializeFill
# ---------------------------------------------------------------------------


class TestDeserializeFill:
    def test_round_trip(self) -> None:
        """fill_to_dict -> deserialize_fill produces equivalent Fill."""
        original = _make_fill()
        payload = fill_to_dict(original)
        result = deserialize_fill(payload)
        assert result.fill_id == original.fill_id
        assert result.order_id == original.order_id
        assert result.route == original.route
        assert result.instrument == original.instrument
        assert result.side == original.side
        assert result.size == original.size
        assert result.price == original.price
        assert result.fee_usdc == original.fee_usdc
        assert result.is_maker == original.is_maker
        assert result.filled_at == original.filled_at
        assert result.trade_id == original.trade_id

    def test_is_maker_string_True(self) -> None:
        fill = _make_fill(is_maker=True)
        payload = fill_to_dict(fill)
        payload["is_maker"] = "True"
        result = deserialize_fill(payload)
        assert result.is_maker is True

    def test_is_maker_string_False(self) -> None:
        fill = _make_fill(is_maker=False)
        payload = fill_to_dict(fill)
        payload["is_maker"] = "False"
        result = deserialize_fill(payload)
        assert result.is_maker is False

    def test_is_maker_string_true_lowercase(self) -> None:
        fill = _make_fill(is_maker=True)
        payload = fill_to_dict(fill)
        payload["is_maker"] = "true"
        result = deserialize_fill(payload)
        assert result.is_maker is True


# ---------------------------------------------------------------------------
# TestDeserializeIdea
# ---------------------------------------------------------------------------


class TestDeserializeIdea:
    def test_round_trip(self) -> None:
        """idea_to_dict -> deserialize_idea produces equivalent RankedTradeIdea."""
        original = _make_trade_idea()
        payload = idea_to_dict(original)
        result = deserialize_idea(payload)
        assert result.idea_id == original.idea_id
        assert result.timestamp == original.timestamp
        assert result.instrument == original.instrument
        assert result.route == original.route
        assert result.direction == original.direction
        assert result.conviction == original.conviction
        assert result.sources == original.sources
        assert result.time_horizon == original.time_horizon
        assert result.entry_price == original.entry_price
        assert result.stop_loss == original.stop_loss
        assert result.take_profit == original.take_profit
        assert result.reasoning == original.reasoning

    def test_optional_fields_none(self) -> None:
        idea = RankedTradeIdea(
            idea_id="idea-002",
            timestamp=T0,
            instrument="BTC-PERP",
            route=Route.B,
            direction=PositionSide.SHORT,
            conviction=0.6,
            sources=[SignalSource.FUNDING_ARB],
            time_horizon=timedelta(hours=4),
            entry_price=None,
            stop_loss=None,
            take_profit=None,
        )
        payload = idea_to_dict(idea)
        result = deserialize_idea(payload)
        assert result.entry_price is None
        assert result.stop_loss is None
        assert result.take_profit is None


# ---------------------------------------------------------------------------
# TestDeserializeSnapshot
# ---------------------------------------------------------------------------


class TestDeserializeSnapshot:
    def test_round_trip(self) -> None:
        """snapshot_to_dict -> deserialize_snapshot produces equivalent MarketSnapshot."""
        original = _make_market_snapshot()
        payload = snapshot_to_dict(original)
        result = deserialize_snapshot(payload)
        assert result.timestamp == original.timestamp
        assert result.instrument == original.instrument
        assert result.mark_price == original.mark_price
        assert result.index_price == original.index_price
        assert result.last_price == original.last_price
        assert result.best_bid == original.best_bid
        assert result.best_ask == original.best_ask
        assert result.spread_bps == original.spread_bps
        assert result.volume_24h == original.volume_24h
        assert result.open_interest == original.open_interest
        assert result.funding_rate == original.funding_rate
        assert result.next_funding_time == original.next_funding_time
        assert result.hours_since_last_funding == original.hours_since_last_funding
        assert result.orderbook_imbalance == original.orderbook_imbalance
        assert result.volatility_1h == original.volatility_1h
        assert result.volatility_24h == original.volatility_24h

    def test_volatility_zero_handled(self) -> None:
        """volatility_1h=0.0 does not raise."""
        snap = _make_market_snapshot()
        payload = snapshot_to_dict(snap)
        payload["volatility_1h"] = 0.0
        result = deserialize_snapshot(payload)
        assert result.volatility_1h == 0.0

    def test_volatility_empty_string_handled(self) -> None:
        """volatility_1h='' (Redis empty string) falls back to 0.0."""
        snap = _make_market_snapshot()
        payload = snapshot_to_dict(snap)
        payload["volatility_1h"] = ""
        result = deserialize_snapshot(payload)
        assert result.volatility_1h == 0.0


# ---------------------------------------------------------------------------
# TestDeserializeSignal
# ---------------------------------------------------------------------------


class TestDeserializeSignal:
    def test_round_trip(self) -> None:
        """signal_to_dict -> deserialize_signal produces equivalent StandardSignal."""
        original = _make_standard_signal()
        payload = signal_to_dict(original)
        result = deserialize_signal(payload)
        assert result.signal_id == original.signal_id
        assert result.timestamp == original.timestamp
        assert result.instrument == original.instrument
        assert result.direction == original.direction
        assert result.conviction == original.conviction
        assert result.source == original.source
        assert result.time_horizon == original.time_horizon
        assert result.reasoning == original.reasoning
        assert result.suggested_route == original.suggested_route
        assert result.entry_price == original.entry_price
        assert result.stop_loss == original.stop_loss
        assert result.take_profit == original.take_profit

    def test_suggested_route_none(self) -> None:
        """suggested_route=None -> None in deserialized result."""
        original = _make_standard_signal(suggested_route=None)
        payload = signal_to_dict(original)
        result = deserialize_signal(payload)
        assert result.suggested_route is None

    def test_optional_price_fields_none(self) -> None:
        """entry_price, stop_loss, take_profit with None -> None after round-trip."""
        original = StandardSignal(
            signal_id="sig-002",
            timestamp=T0,
            instrument="BTC-PERP",
            direction=PositionSide.SHORT,
            conviction=0.7,
            source=SignalSource.MEAN_REVERSION,
            time_horizon=timedelta(minutes=30),
            reasoning="Mean reversion",
            suggested_route=None,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
        )
        payload = signal_to_dict(original)
        result = deserialize_signal(payload)
        assert result.entry_price is None
        assert result.stop_loss is None
        assert result.take_profit is None


# ---------------------------------------------------------------------------
# TestDeserializePortfolioSnapshot
# ---------------------------------------------------------------------------


class TestDeserializePortfolioSnapshot:
    def test_round_trip(self) -> None:
        """portfolio_snapshot_to_dict -> deserialize_portfolio_snapshot produces equivalent."""
        original = _make_portfolio_snapshot()
        payload = portfolio_snapshot_to_dict(original)
        result = deserialize_portfolio_snapshot(payload)
        assert result.timestamp == original.timestamp
        assert result.route == original.route
        assert result.equity_usdc == original.equity_usdc
        assert result.used_margin_usdc == original.used_margin_usdc
        assert result.available_margin_usdc == original.available_margin_usdc
        assert result.margin_utilization_pct == original.margin_utilization_pct
        assert result.unrealized_pnl_usdc == original.unrealized_pnl_usdc
        assert result.realized_pnl_today_usdc == original.realized_pnl_today_usdc
        assert result.funding_pnl_today_usdc == original.funding_pnl_today_usdc
        assert result.fees_paid_today_usdc == original.fees_paid_today_usdc
        # positions is always empty after deserialization (not serialized)
        assert result.positions == []

    def test_route_b_round_trip(self) -> None:
        snap = _make_portfolio_snapshot()
        snap.route = Route.B
        payload = portfolio_snapshot_to_dict(snap)
        result = deserialize_portfolio_snapshot(payload)
        assert result.route == Route.B


# ---------------------------------------------------------------------------
# TestDeserializeFundingPayment
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestIdeaMetadata (SAFE-03)
# ---------------------------------------------------------------------------


class TestIdeaMetadata:
    """SAFE-03: metadata field must survive Redis round-trips through idea_to_dict / deserialize_idea."""

    def _make_idea_with_metadata(self, metadata: dict) -> RankedTradeIdea:
        return RankedTradeIdea(
            idea_id="idea-meta-001",
            timestamp=T0,
            instrument="ETH-PERP",
            route=Route.A,
            direction=PositionSide.LONG,
            conviction=0.80,
            sources=[SignalSource.MOMENTUM],
            time_horizon=timedelta(hours=2),
            metadata=metadata,
        )

    def test_idea_to_dict_includes_metadata(self) -> None:
        """idea_to_dict must include a 'metadata' key in its output."""
        idea = self._make_idea_with_metadata({"regime": "trending", "contributing_signals": 3})
        result = idea_to_dict(idea)
        assert "metadata" in result, "SAFE-03: 'metadata' key missing from idea_to_dict output"
        assert result["metadata"]["regime"] == "trending"
        assert result["metadata"]["contributing_signals"] == 3

    def test_idea_metadata_round_trip(self) -> None:
        """Full round-trip: str/int metadata values are preserved exactly."""
        idea = self._make_idea_with_metadata({"regime": "ranging", "contributing_signals": 2})
        result = deserialize_idea(idea_to_dict(idea))
        assert result.metadata == {"regime": "ranging", "contributing_signals": 2}

    def test_idea_metadata_decimal_serialization(self) -> None:
        """Decimal values in metadata must be serialized as str (not Decimal) to avoid orjson TypeError."""
        idea = self._make_idea_with_metadata({"funding_rate": Decimal("0.0003")})
        serialized = idea_to_dict(idea)
        assert serialized["metadata"]["funding_rate"] == "0.0003", (
            "SAFE-03: Decimal in metadata must be converted to str for JSON serialization"
        )
        assert not isinstance(serialized["metadata"]["funding_rate"], Decimal)

    def test_idea_empty_metadata_round_trip(self) -> None:
        """Empty metadata {} must round-trip to empty dict."""
        idea = self._make_idea_with_metadata({})
        result = deserialize_idea(idea_to_dict(idea))
        assert result.metadata == {}

    def test_idea_metadata_mixed_types(self) -> None:
        """str, int, float, None values in metadata pass through unchanged."""
        meta = {"name": "trending", "count": 5, "score": 0.75, "extra": None}
        idea = self._make_idea_with_metadata(meta)
        serialized = idea_to_dict(idea)
        assert serialized["metadata"]["name"] == "trending"
        assert serialized["metadata"]["count"] == 5
        assert serialized["metadata"]["score"] == 0.75
        assert serialized["metadata"]["extra"] is None


# ---------------------------------------------------------------------------
# TestDeserializeFundingPayment
# ---------------------------------------------------------------------------


class TestDeserializeFundingPayment:
    def test_round_trip(self) -> None:
        """funding_payment_to_dict -> deserialize_funding_payment produces equivalent."""
        original = _make_funding_payment()
        payload = funding_payment_to_dict(original)
        result = deserialize_funding_payment(payload)
        assert result.timestamp == original.timestamp
        assert result.instrument == original.instrument
        assert result.route == original.route
        assert result.rate == original.rate
        assert result.payment_usdc == original.payment_usdc
        assert result.position_size == original.position_size
        assert result.position_side == original.position_side
        assert result.cumulative_24h_usdc == original.cumulative_24h_usdc

    def test_short_position_side(self) -> None:
        payment = FundingPayment(
            timestamp=T0,
            instrument="BTC-PERP",
            route=Route.B,
            rate=Decimal("-0.0002"),
            payment_usdc=Decimal("3.00"),
            position_size=Decimal("0.5"),
            position_side=PositionSide.SHORT,
            cumulative_24h_usdc=Decimal("12.00"),
        )
        payload = funding_payment_to_dict(payment)
        result = deserialize_funding_payment(payload)
        assert result.position_side == PositionSide.SHORT
        assert result.route == Route.B
