"""Tests for monitoring agent serialization helpers."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.common.models.enums import (
    OrderSide,
    PortfolioTarget,
    PositionSide,
)
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill
from libs.common.models.portfolio import PortfolioSnapshot

from agents.monitoring.alerting import Alert, AlertSeverity, AlertType
from agents.monitoring.main import (
    alert_to_dict,
    deserialize_alert,
    deserialize_fill,
    deserialize_funding_payment,
    deserialize_portfolio_snapshot,
)

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestPortfolioSnapshotDeserialization:
    def test_roundtrip_with_reconciliation_format(self) -> None:
        """Verify we can deserialize what reconciliation agent produces."""
        from agents.reconciliation.main import portfolio_snapshot_to_dict

        original = PortfolioSnapshot(
            timestamp=T0,
            portfolio_target=PortfolioTarget.A,
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

        assert reconstructed.portfolio_target == PortfolioTarget.A
        assert reconstructed.equity_usdc == Decimal("10000")
        assert reconstructed.used_margin_usdc == Decimal("3000")
        assert reconstructed.margin_utilization_pct == 30.0
        assert reconstructed.unrealized_pnl_usdc == Decimal("200")
        assert reconstructed.realized_pnl_today_usdc == Decimal("50")
        assert reconstructed.funding_pnl_today_usdc == Decimal("-10")
        assert reconstructed.fees_paid_today_usdc == Decimal("5")


class TestFundingPaymentDeserialization:
    def test_roundtrip_with_reconciliation_format(self) -> None:
        """Verify we can deserialize what reconciliation agent produces."""
        from agents.reconciliation.main import funding_payment_to_dict

        original = FundingPayment(
            timestamp=T0,
            instrument="ETH-PERP",
            portfolio_target=PortfolioTarget.A,
            rate=Decimal("0.0001"),
            payment_usdc=Decimal("-0.50"),
            position_size=Decimal("2.5"),
            position_side=PositionSide.LONG,
            cumulative_24h_usdc=Decimal("-5.00"),
        )
        serialized = funding_payment_to_dict(original)
        reconstructed = deserialize_funding_payment(serialized)

        assert reconstructed.instrument == "ETH-PERP"
        assert reconstructed.rate == Decimal("0.0001")
        assert reconstructed.payment_usdc == Decimal("-0.50")
        assert reconstructed.position_side == PositionSide.LONG
        assert reconstructed.cumulative_24h_usdc == Decimal("-5.00")


class TestFillDeserialization:
    def test_roundtrip_with_execution_format(self) -> None:
        """Verify we can deserialize what execution agent produces."""
        from agents.execution.main import fill_to_dict

        original = Fill(
            fill_id="fill-001",
            order_id="ord-001",
            portfolio_target=PortfolioTarget.A,
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

        assert reconstructed.fill_id == "fill-001"
        assert reconstructed.side == OrderSide.BUY
        assert reconstructed.size == Decimal("2.5")
        assert reconstructed.fee_usdc == Decimal("0.69")
        assert reconstructed.is_maker is True


class TestAlertSerialization:
    def test_roundtrip_with_portfolio(self) -> None:
        original = Alert(
            alert_type=AlertType.MARGIN_HIGH,
            severity=AlertSeverity.WARNING,
            portfolio_target=PortfolioTarget.A,
            message="Margin utilization 55.0%",
            timestamp=T0,
            value=55.0,
            threshold=50.0,
        )
        serialized = alert_to_dict(original)
        reconstructed = deserialize_alert(serialized)

        assert reconstructed.alert_type == AlertType.MARGIN_HIGH
        assert reconstructed.severity == AlertSeverity.WARNING
        assert reconstructed.portfolio_target == PortfolioTarget.A
        assert reconstructed.message == "Margin utilization 55.0%"
        assert reconstructed.value == 55.0
        assert reconstructed.threshold == 50.0

    def test_roundtrip_without_portfolio(self) -> None:
        original = Alert(
            alert_type=AlertType.OPPOSING_POSITIONS,
            severity=AlertSeverity.INFO,
            portfolio_target=None,
            message="Opposing positions: A is LONG, B is SHORT",
            timestamp=T0,
        )
        serialized = alert_to_dict(original)
        reconstructed = deserialize_alert(serialized)

        assert reconstructed.portfolio_target is None
        assert reconstructed.value is None
        assert reconstructed.threshold is None

    def test_roundtrip_critical_alert(self) -> None:
        original = Alert(
            alert_type=AlertType.LIQUIDATION_CLOSE,
            severity=AlertSeverity.CRITICAL,
            portfolio_target=PortfolioTarget.B,
            message="Liquidation 4.5% away",
            timestamp=T0,
            value=4.5,
            threshold=8.0,
        )
        serialized = alert_to_dict(original)
        reconstructed = deserialize_alert(serialized)

        assert reconstructed.severity == AlertSeverity.CRITICAL
        assert reconstructed.portfolio_target == PortfolioTarget.B
