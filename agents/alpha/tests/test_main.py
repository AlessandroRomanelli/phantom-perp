"""Tests for alpha agent serialization helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from libs.common.models.enums import Route, PositionSide, SignalSource
from libs.common.models.signal import StandardSignal
from libs.common.models.trade_idea import RankedTradeIdea

from agents.alpha.main import deserialize_signal, idea_to_dict


class TestDeserializeSignal:
    def test_roundtrip(self) -> None:
        """Serialize via signal_to_dict format, then deserialize."""
        payload = {
            "signal_id": "sig-123",
            "timestamp": "2025-06-15T12:00:00+00:00",
            "instrument": "ETH-PERP",
            "direction": "LONG",
            "conviction": 0.78,
            "source": "momentum",
            "time_horizon_seconds": 7200,
            "reasoning": "Breakout detected",
            "suggested_route": "autonomous",
            "entry_price": "2232.50",
            "stop_loss": "2188.00",
            "take_profit": "2320.00",
            "metadata": {"rsi": 65},
        }
        signal = deserialize_signal(payload)

        assert signal.signal_id == "sig-123"
        assert signal.direction == PositionSide.LONG
        assert signal.conviction == 0.78
        assert signal.source == SignalSource.MOMENTUM
        assert signal.time_horizon == timedelta(hours=2)
        assert signal.suggested_route == Route.A
        assert signal.entry_price == Decimal("2232.50")
        assert signal.stop_loss == Decimal("2188.00")
        assert signal.take_profit == Decimal("2320.00")

    def test_none_optional_fields(self) -> None:
        payload = {
            "signal_id": "sig-456",
            "timestamp": "2025-06-15T12:00:00+00:00",
            "instrument": "ETH-PERP",
            "direction": "SHORT",
            "conviction": 0.6,
            "source": "funding_arb",
            "time_horizon_seconds": 3600,
            "reasoning": "Funding elevated",
        }
        signal = deserialize_signal(payload)

        assert signal.suggested_route is None
        assert signal.entry_price is None
        assert signal.stop_loss is None
        assert signal.take_profit is None


class TestIdeaToDict:
    def test_serializes_all_fields(self) -> None:
        idea = RankedTradeIdea(
            idea_id="idea-001",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            instrument="ETH-PERP",
            route=Route.A,
            direction=PositionSide.LONG,
            conviction=0.82,
            sources=[SignalSource.MOMENTUM, SignalSource.SENTIMENT],
            time_horizon=timedelta(hours=2),
            entry_price=Decimal("2200"),
            stop_loss=Decimal("2150"),
            take_profit=Decimal("2350"),
            reasoning="Strong momentum with positive sentiment",
        )
        d = idea_to_dict(idea)

        assert d["idea_id"] == "idea-001"
        assert d["route"] == "autonomous"
        assert d["direction"] == "LONG"
        assert d["conviction"] == 0.82
        assert d["sources"] == "momentum,sentiment"
        assert d["time_horizon_seconds"] == 7200
        assert d["entry_price"] == "2200"
        assert d["stop_loss"] == "2150"
        assert d["take_profit"] == "2350"

    def test_matches_risk_deserialize_format(self) -> None:
        """The dict we produce must be deserializable by risk agent."""
        idea = RankedTradeIdea(
            idea_id="idea-002",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            instrument="ETH-PERP",
            route=Route.B,
            direction=PositionSide.SHORT,
            conviction=0.65,
            sources=[SignalSource.FUNDING_ARB],
            time_horizon=timedelta(hours=1),
            reasoning="Elevated funding rate",
        )
        d = idea_to_dict(idea)

        # Verify risk agent can reconstruct it
        from agents.risk.main import deserialize_idea

        reconstructed = deserialize_idea(d)
        assert reconstructed.idea_id == "idea-002"
        assert reconstructed.route == Route.B
        assert reconstructed.direction == PositionSide.SHORT
        assert reconstructed.conviction == 0.65
        assert reconstructed.sources == [SignalSource.FUNDING_ARB]
        assert reconstructed.time_horizon == timedelta(hours=1)
        assert reconstructed.reasoning == "Elevated funding rate"

    def test_none_prices_serialized_as_none(self) -> None:
        idea = RankedTradeIdea(
            idea_id="idea-003",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            instrument="ETH-PERP",
            route=Route.A,
            direction=PositionSide.LONG,
            conviction=0.5,
            sources=[SignalSource.MOMENTUM],
            time_horizon=timedelta(hours=2),
        )
        d = idea_to_dict(idea)

        assert d["entry_price"] is None
        assert d["stop_loss"] is None
        assert d["take_profit"] is None

        # Risk agent should also handle None prices
        from agents.risk.main import deserialize_idea

        reconstructed = deserialize_idea(d)
        assert reconstructed.entry_price is None
        assert reconstructed.stop_loss is None
        assert reconstructed.take_profit is None
