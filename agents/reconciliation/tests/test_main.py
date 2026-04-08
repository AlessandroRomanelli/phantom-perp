"""Tests for reconciliation agent serialization helpers and credential routing."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from libs.common.models.enums import (
    OrderSide,
    Route,
    PositionSide,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill
from libs.common.models.portfolio import PortfolioSnapshot

from libs.common.serialization import (
    deserialize_fill,
    deserialize_funding_payment,
    deserialize_portfolio_snapshot,
    fill_to_dict,
    funding_payment_to_dict,
    portfolio_snapshot_to_dict,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestFillDeserialization:
    def test_roundtrip_with_execution_agent_format(self) -> None:
        """Verify we can deserialize what execution agent's fill_to_dict produces."""
        original = Fill(
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
        serialized = fill_to_dict(original)
        reconstructed = deserialize_fill(serialized)

        assert reconstructed.fill_id == original.fill_id
        assert reconstructed.order_id == original.order_id
        assert reconstructed.route == Route.A
        assert reconstructed.side == OrderSide.BUY
        assert reconstructed.size == Decimal("2.5")
        assert reconstructed.price == Decimal("2200")
        assert reconstructed.fee_usdc == Decimal("0.69")
        assert reconstructed.is_maker is True
        assert reconstructed.trade_id == "trade-001"

    def test_taker_fill(self) -> None:
        original = Fill(
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
        serialized = fill_to_dict(original)
        reconstructed = deserialize_fill(serialized)
        assert reconstructed.is_maker is False
        assert reconstructed.side == OrderSide.SELL
        assert reconstructed.route == Route.B


class TestPortfolioSnapshotSerialization:
    def test_roundtrip(self) -> None:
        original = PortfolioSnapshot(
            timestamp=T0,
            route=Route.A,
            equity_usdc=Decimal("10000"),
            used_margin_usdc=Decimal("3000"),
            available_margin_usdc=Decimal("7000"),
            margin_utilization_pct=30.0,
            positions=[],
            unrealized_pnl_usdc=Decimal("200"),
            realized_pnl_today_usdc=Decimal("50"),
            funding_pnl_today_usdc=Decimal("-10"),
            fees_paid_today_usdc=Decimal("5"),
        )
        serialized = portfolio_snapshot_to_dict(original)
        reconstructed = deserialize_portfolio_snapshot(serialized)

        assert reconstructed.route == Route.A
        assert reconstructed.equity_usdc == Decimal("10000")
        assert reconstructed.used_margin_usdc == Decimal("3000")
        assert reconstructed.available_margin_usdc == Decimal("7000")
        assert reconstructed.margin_utilization_pct == 30.0
        assert reconstructed.unrealized_pnl_usdc == Decimal("200")
        assert reconstructed.realized_pnl_today_usdc == Decimal("50")
        assert reconstructed.funding_pnl_today_usdc == Decimal("-10")
        assert reconstructed.fees_paid_today_usdc == Decimal("5")
        # positions not sent over stream
        assert reconstructed.positions == []

    def test_includes_position_count(self) -> None:
        snap = PortfolioSnapshot(
            timestamp=T0,
            route=Route.B,
            equity_usdc=Decimal("5000"),
            used_margin_usdc=Decimal("0"),
            available_margin_usdc=Decimal("5000"),
            margin_utilization_pct=0.0,
            positions=[],
            unrealized_pnl_usdc=Decimal("0"),
            realized_pnl_today_usdc=Decimal("0"),
            funding_pnl_today_usdc=Decimal("0"),
            fees_paid_today_usdc=Decimal("0"),
        )
        serialized = portfolio_snapshot_to_dict(snap)
        assert serialized["position_count"] == 0


class TestFundingPaymentSerialization:
    def test_roundtrip(self) -> None:
        original = FundingPayment(
            timestamp=T0,
            instrument="ETH-PERP",
            route=Route.A,
            rate=Decimal("0.0001"),
            payment_usdc=Decimal("-0.50"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            cumulative_24h_usdc=Decimal("-5.00"),
        )
        serialized = funding_payment_to_dict(original)
        reconstructed = deserialize_funding_payment(serialized)

        assert reconstructed.instrument == "ETH-PERP"
        assert reconstructed.route == Route.A
        assert reconstructed.rate == Decimal("0.0001")
        assert reconstructed.payment_usdc == Decimal("-0.50")
        assert reconstructed.position_size == Decimal("2.5")
        assert reconstructed.position_side == PositionSide.LONG
        assert reconstructed.cumulative_24h_usdc == Decimal("-5.00")

    def test_short_position_payment(self) -> None:
        original = FundingPayment(
            timestamp=T0,
            instrument="ETH-PERP",
            route=Route.B,
            rate=Decimal("0.0002"),
            payment_usdc=Decimal("1.00"),
            position_size=Decimal("1.0"),
            position_side=PositionSide.SHORT,
            cumulative_24h_usdc=Decimal("12.00"),
        )
        serialized = funding_payment_to_dict(original)
        reconstructed = deserialize_funding_payment(serialized)
        assert reconstructed.position_side == PositionSide.SHORT
        assert reconstructed.payment_usdc == Decimal("1.00")
        assert reconstructed.route == Route.B


class TestRouteBCredentialRouting:
    """Tests for Route B client credential routing in _create_live_clients (SAFE-05)."""

    def test_route_b_uses_separate_credentials_when_set(self) -> None:
        """When api_key_b is set, a separate CoinbaseAuth is created for Route B."""
        from agents.reconciliation.main import _create_live_clients

        mock_settings = MagicMock()
        mock_settings.coinbase.api_key_a = "key_a"
        mock_settings.coinbase.api_secret_a = "secret_a"
        mock_settings.coinbase.api_key_b = "key_b"
        mock_settings.coinbase.api_secret_b = "secret_b"
        mock_settings.coinbase.rest_url = "https://api.coinbase.com"

        mock_client_a = MagicMock()
        mock_client_b = MagicMock()

        with patch("agents.reconciliation.main.CoinbaseAuth") as MockAuth, \
             patch("agents.reconciliation.main.CoinbaseRESTClient") as MockClient, \
             patch("agents.reconciliation.main.RateLimiter"):
            MockClient.side_effect = [mock_client_a, mock_client_b]

            client_a, client_b = _create_live_clients(mock_settings)

            # Two separate CoinbaseAuth instances created
            assert MockAuth.call_count == 2
            assert MockAuth.call_args_list[0].kwargs["api_key"] == "key_a"
            assert MockAuth.call_args_list[1].kwargs["api_key"] == "key_b"
            # client_a and client_b are different objects
            assert client_a is not client_b

    def test_route_b_falls_back_to_route_a_credentials(self) -> None:
        """When api_key_b is empty, the same client object is used for both routes."""
        from agents.reconciliation.main import _create_live_clients

        mock_settings = MagicMock()
        mock_settings.coinbase.api_key_a = "key_a"
        mock_settings.coinbase.api_secret_a = "secret_a"
        mock_settings.coinbase.api_key_b = ""
        mock_settings.coinbase.api_secret_b = ""
        mock_settings.coinbase.rest_url = "https://api.coinbase.com"

        mock_client = MagicMock()

        with patch("agents.reconciliation.main.CoinbaseAuth") as MockAuth, \
             patch("agents.reconciliation.main.CoinbaseRESTClient") as MockClient, \
             patch("agents.reconciliation.main.RateLimiter"):
            MockClient.return_value = mock_client

            client_a, client_b = _create_live_clients(mock_settings)

            # Only one CoinbaseAuth instance — Route A credentials only
            assert MockAuth.call_count == 1
            assert MockAuth.call_args_list[0].kwargs["api_key"] == "key_a"
            # Same client object for both routes
            assert client_a is client_b
