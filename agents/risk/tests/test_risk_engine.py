"""Comprehensive tests for the risk engine — every rejection scenario.

Each test class isolates a single risk check. Helper fixtures create
valid baseline data that passes all checks, then individual tests
modify one parameter to trigger a specific rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.common.constants import (
    FUNDING_RATE_CIRCUIT_BREAKER_PCT,
    STALE_DATA_HALT_SECONDS,
)
from libs.common.instruments import load_instruments
from libs.common.models.enums import (
    Route,
    PositionSide,
    SignalSource,
)
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.position import PerpPosition
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.utils import utc_now

from agents.risk.limits import RiskLimits
from agents.risk.main import RiskEngine

TEST_INSTRUMENT_ID = "ETH-PERP"

# Load instrument registry for get_instrument() calls inside RiskEngine.evaluate()
load_instruments({
    "instruments": [{
        "id": "ETH-PERP",
        "base_currency": "ETH",
        "quote_currency": "USDC",
        "tick_size": 0.01,
        "min_order_size": 0.0001,
    }]
})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIMITS_A = RiskLimits(
    max_leverage=Decimal("5"),
    max_position_notional_usdc=Decimal("6000"),
    max_position_pct_equity=Decimal("40"),
    max_margin_utilization_pct=Decimal("70"),
    min_liquidation_distance_pct=Decimal("8"),
    max_daily_loss_pct=Decimal("10"),
    max_drawdown_pct=Decimal("25"),
    stop_loss_required=True,
    max_concurrent_positions=3,
    max_funding_cost_per_day_usdc=Decimal("20"),
)

LIMITS_B = RiskLimits(
    max_leverage=Decimal("3"),
    max_position_notional_usdc=Decimal("16000"),
    max_position_pct_equity=Decimal("25"),
    max_margin_utilization_pct=Decimal("50"),
    min_liquidation_distance_pct=Decimal("15"),
    max_daily_loss_pct=Decimal("5"),
    max_drawdown_pct=Decimal("15"),
    stop_loss_required=True,
    max_concurrent_positions=3,
    max_funding_cost_per_day_usdc=Decimal("100"),
)


def _engine() -> RiskEngine:
    return RiskEngine(LIMITS_A, LIMITS_B)


def _idea(
    target: Route = Route.A,
    direction: PositionSide = PositionSide.LONG,
    conviction: float = 0.8,
    entry_price: Decimal = Decimal("2000"),
    stop_loss: Decimal | None = Decimal("1900"),
    take_profit: Decimal | None = Decimal("2200"),
    time_horizon: timedelta = timedelta(hours=4),
    sources: list[SignalSource] | None = None,
) -> RankedTradeIdea:
    return RankedTradeIdea(
        idea_id="test-idea-001",
        timestamp=utc_now(),
        instrument=TEST_INSTRUMENT_ID,
        route=target,
        direction=direction,
        conviction=conviction,
        sources=sources or [SignalSource.MOMENTUM],
        time_horizon=time_horizon,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasoning="Test idea",
    )


def _portfolio(
    target: Route = Route.A,
    equity: Decimal = Decimal("10000"),
    used_margin: Decimal = Decimal("0"),
    positions: list[PerpPosition] | None = None,
    net_pnl_today: Decimal = Decimal("0"),
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=utc_now(),
        route=target,
        equity_usdc=equity,
        used_margin_usdc=used_margin,
        available_margin_usdc=equity - used_margin,
        margin_utilization_pct=float(used_margin / equity * 100) if equity else 0.0,
        positions=positions or [],
        unrealized_pnl_usdc=Decimal("0"),
        realized_pnl_today_usdc=net_pnl_today,
        funding_pnl_today_usdc=Decimal("0"),
        fees_paid_today_usdc=Decimal("0"),
    )


def _make_position(
    size: Decimal = Decimal("1"),
    mark: Decimal = Decimal("2000"),
    side: PositionSide = PositionSide.LONG,
    target: Route = Route.A,
) -> PerpPosition:
    return PerpPosition(
        instrument=TEST_INSTRUMENT_ID,
        route=target,
        side=side,
        size=size,
        entry_price=mark,
        mark_price=mark,
        unrealized_pnl_usdc=Decimal("0"),
        realized_pnl_usdc=Decimal("0"),
        leverage=Decimal("1"),
        initial_margin_usdc=Decimal("400"),
        maintenance_margin_usdc=Decimal("20"),
        liquidation_price=Decimal("1600"),
        margin_ratio=0.04,
        cumulative_funding_usdc=Decimal("0"),
        total_fees_usdc=Decimal("0"),
    )


# Defaults for market data
MARKET_PRICE = Decimal("2000")
FUNDING_RATE = Decimal("0.0001")  # Normal hourly rate


# ---------------------------------------------------------------------------
# Stale Data Halt
# ---------------------------------------------------------------------------

class TestStaleDataHalt:
    def test_stale_data_rejected(self) -> None:
        """Market data older than 30s → rejected."""
        engine = _engine()
        stale_ts = utc_now() - timedelta(seconds=STALE_DATA_HALT_SECONDS + 5)

        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, stale_ts, FUNDING_RATE,
        )
        assert result.approved is False
        assert "Stale" in (result.rejection_reason or "")

    def test_fresh_data_passes(self) -> None:
        engine = _engine()
        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "Stale" not in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# Funding Rate Circuit Breaker
# ---------------------------------------------------------------------------

class TestFundingCircuitBreaker:
    def test_extreme_positive_rate_rejected(self) -> None:
        engine = _engine()
        extreme_rate = FUNDING_RATE_CIRCUIT_BREAKER_PCT  # Exactly at threshold

        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), extreme_rate,
        )
        assert result.approved is False
        assert "circuit breaker" in (result.rejection_reason or "").lower()

    def test_extreme_negative_rate_rejected(self) -> None:
        engine = _engine()
        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(),
            -FUNDING_RATE_CIRCUIT_BREAKER_PCT,
        )
        assert result.approved is False
        assert "circuit breaker" in (result.rejection_reason or "").lower()

    def test_normal_rate_passes(self) -> None:
        engine = _engine()
        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), Decimal("0.0001"),
        )
        assert "circuit breaker" not in (result.rejection_reason or "").lower()


# ---------------------------------------------------------------------------
# Mandatory Stop-Loss
# ---------------------------------------------------------------------------

class TestStopLossRequired:
    def test_no_stop_loss_rejected(self) -> None:
        engine = _engine()
        idea = _idea(stop_loss=None)

        result = engine.evaluate(
            idea, _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False
        assert "stop-loss" in (result.rejection_reason or "").lower()

    def test_with_stop_loss_passes(self) -> None:
        engine = _engine()
        idea = _idea(stop_loss=Decimal("1900"))

        result = engine.evaluate(
            idea, _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "stop-loss" not in (result.rejection_reason or "").lower()


# ---------------------------------------------------------------------------
# Daily Loss Kill Switch
# ---------------------------------------------------------------------------

class TestDailyLossKillSwitch:
    def test_route_a_daily_loss_exceeded(self) -> None:
        """A daily loss > 10% → Portfolio A halted."""
        engine = _engine()
        # net_pnl = -1500 on 10000 equity → 15% loss > 10% limit
        state = _portfolio(equity=Decimal("10000"), net_pnl_today=Decimal("-1500"))

        result = engine.evaluate(
            _idea(), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False
        assert "Daily loss" in (result.rejection_reason or "")

    def test_route_b_daily_loss_exceeded(self) -> None:
        """B has stricter limit: > 5% → halted."""
        engine = _engine()
        # 6% loss on B (> 5% limit)
        state = _portfolio(
            target=Route.B,
            equity=Decimal("10000"),
            net_pnl_today=Decimal("-600"),
        )

        result = engine.evaluate(
            _idea(target=Route.B), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False
        assert "Daily loss" in (result.rejection_reason or "")

    def test_within_daily_loss_limit_passes(self) -> None:
        engine = _engine()
        # 5% loss on A (within 10% limit)
        state = _portfolio(equity=Decimal("10000"), net_pnl_today=Decimal("-500"))

        result = engine.evaluate(
            _idea(), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "Daily loss" not in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# Max Concurrent Positions
# ---------------------------------------------------------------------------

class TestMaxConcurrentPositions:
    def test_exceeds_max_concurrent_rejected(self) -> None:
        engine = _engine()
        positions = [_make_position() for _ in range(3)]  # At limit
        state = _portfolio(positions=positions)

        result = engine.evaluate(
            _idea(), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False
        assert "concurrent" in (result.rejection_reason or "").lower()

    def test_within_concurrent_limit_passes(self) -> None:
        engine = _engine()
        positions = [_make_position()]  # 1 of 3
        state = _portfolio(positions=positions, used_margin=Decimal("400"))

        result = engine.evaluate(
            _idea(), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "concurrent" not in (result.rejection_reason or "").lower()


# ---------------------------------------------------------------------------
# Leverage Limits
# ---------------------------------------------------------------------------

class TestLeverageLimits:
    def test_route_a_exceeds_5x_rejected(self) -> None:
        """Existing position already at 5x leverage → any new trade rejected."""
        engine = _engine()
        # Existing: 2.5 ETH × 2000 = 5000 notional on 1000 equity → exactly 5x
        # Any new position pushes leverage beyond 5x → sizer returns 0 → rejected
        pos = _make_position(size=Decimal("2.5"), mark=Decimal("2000"))
        state = _portfolio(
            equity=Decimal("1000"),
            used_margin=Decimal("500"),
            positions=[pos],
        )

        result = engine.evaluate(
            _idea(conviction=1.0), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False

    def test_route_b_3x_limit_stricter(self) -> None:
        """Portfolio B limit is 3x, stricter than A's 5x."""
        engine = _engine()
        # Equity 5000, existing 2 ETH × 2000 = 4000 notional → leverage already ~0.8x
        # With B's 25% equity pct, max notional = 1250 → new size ~0.62 ETH
        # Total notional = 4000 + 1250 = 5250 → leverage 1.05x (within 3x)
        # But if we have higher existing: 2.5 ETH = 5000 notional → leverage 1.0x
        # New = 1250 → total 6250 → 1.25x (still ok)
        # Need a case where adding new would breach 3x:
        # Existing 2 ETH × 5000 = 10000 notional on 5000 equity → already 2x
        # Adding 25% of 5000 = 1250 → 0.25 ETH × 5000 = 1250 → total 11250 → 2.25x
        # Still within 3x. Need more extreme values.
        pos = _make_position(
            size=Decimal("2.5"), mark=Decimal("5000"), target=Route.B,
        )
        state = _portfolio(
            target=Route.B,
            equity=Decimal("5000"),
            used_margin=Decimal("4200"),
            positions=[pos],
        )

        result = engine.evaluate(
            _idea(target=Route.B, entry_price=Decimal("5000"), conviction=1.0),
            state, Decimal("5000"), utc_now(), FUNDING_RATE,
        )
        # Should be rejected (margin utilization or leverage)
        assert result.approved is False


# ---------------------------------------------------------------------------
# Margin Utilization
# ---------------------------------------------------------------------------

class TestMarginUtilization:
    def test_exceeds_max_utilization_rejected(self) -> None:
        """Margin already at limit → no room for any new order."""
        engine = _engine()
        # A's limit is 70%. Equity 10000, used 7000 → 70%.
        # available margin = 7000 - 7000 = 0 → rejected
        state = _portfolio(equity=Decimal("10000"), used_margin=Decimal("7000"))

        result = engine.evaluate(
            _idea(), state, MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False


# ---------------------------------------------------------------------------
# Liquidation Distance
# ---------------------------------------------------------------------------

class TestLiquidationDistance:
    def test_route_a_8pct_floor(self) -> None:
        """Portfolio A requires at least 8% liquidation distance."""
        engine = _engine()
        # At 5x leverage LONG, liq distance is ~19.5% → passes 8%
        # This should pass for A
        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "Liquidation distance" not in (result.rejection_reason or "")

    def test_route_b_15pct_floor(self) -> None:
        """Portfolio B requires at least 15% liquidation distance.
        At 3x leverage, distance is ~32% → should pass."""
        engine = _engine()
        result = engine.evaluate(
            _idea(target=Route.B),
            _portfolio(target=Route.B),
            MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "Liquidation distance" not in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# Liquidation Guard (stop-loss before liquidation)
# ---------------------------------------------------------------------------

class TestLiquidationGuard:
    def test_stop_beyond_liquidation_rejected(self) -> None:
        """Stop-loss set beyond the liquidation price → rejected."""
        engine = _engine()
        # At 5x LONG entry 2000, liq ~1616. Stop at 1500 (below liq) → bad.
        idea = _idea(stop_loss=Decimal("1500"))

        result = engine.evaluate(
            idea, _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is False
        assert "liquidation" in (result.rejection_reason or "").lower()

    def test_stop_before_liquidation_passes(self) -> None:
        """Stop-loss between entry and liquidation → safe."""
        engine = _engine()
        # At 5x LONG entry 2000, liq ~1616. Stop at 1900 → safe.
        idea = _idea(stop_loss=Decimal("1900"))

        result = engine.evaluate(
            idea, _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert "would not trigger before" not in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# Funding Cost Projection
# ---------------------------------------------------------------------------

class TestFundingCostProjection:
    def test_exceeds_daily_budget_rejected(self) -> None:
        """Projected daily funding cost exceeds limit → rejected."""
        engine = _engine()
        # Rate must be below circuit breaker (0.0005) but high enough that
        # daily funding cost exceeds $20 limit for A.
        # daily_cost = size * price * rate * 24
        # With size ~2 ETH, price 2000, rate 0.0004:
        # daily = 2 * 2000 * 0.0004 * 24 = 38.4 > 20
        high_rate = Decimal("0.0004")

        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), high_rate,
        )
        assert result.approved is False
        assert "funding cost" in (result.rejection_reason or "").lower()

    def test_receiving_funding_always_passes(self) -> None:
        """When position receives funding (not paying), no cost to check."""
        engine = _engine()
        # SHORT + positive rate → receives funding
        result = engine.evaluate(
            _idea(direction=PositionSide.SHORT, stop_loss=Decimal("2100"), take_profit=Decimal("1800")),
            _portfolio(),
            MARKET_PRICE, utc_now(),
            Decimal("0.001"),  # High rate but SHORT receives it
        )
        assert "funding cost" not in (result.rejection_reason or "").lower()


# ---------------------------------------------------------------------------
# Happy Path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_route_a_order_approved(self) -> None:
        """Standard Portfolio A trade passes all checks."""
        engine = _engine()
        result = engine.evaluate(
            _idea(target=Route.A),
            _portfolio(target=Route.A),
            MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is True
        assert result.critical is False
        assert result.proposed_order is not None

        order = result.proposed_order
        assert order.route == Route.A
        assert order.size > Decimal("0")
        assert order.stop_loss == Decimal("1900")
        assert order.take_profit == Decimal("2200")
        assert order.estimated_fee_usdc > Decimal("0")
        assert order.estimated_margin_required_usdc > Decimal("0")
        assert order.estimated_liquidation_price > Decimal("0")

    def test_route_b_order_approved(self) -> None:
        """Standard Portfolio B trade passes all checks."""
        engine = _engine()
        result = engine.evaluate(
            _idea(target=Route.B),
            _portfolio(target=Route.B),
            MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.approved is True
        assert result.proposed_order is not None

        order = result.proposed_order
        assert order.route == Route.B

    def test_approved_order_has_correct_side(self) -> None:
        engine = _engine()
        long_result = engine.evaluate(
            _idea(direction=PositionSide.LONG),
            _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert long_result.proposed_order is not None
        assert long_result.proposed_order.side.value == "BUY"

        short_result = engine.evaluate(
            _idea(direction=PositionSide.SHORT, stop_loss=Decimal("2100"), take_profit=Decimal("1800")),
            _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert short_result.proposed_order is not None
        assert short_result.proposed_order.side.value == "SELL"

    def test_different_limits_applied_per_portfolio(self) -> None:
        """Portfolio A and B should produce orders with different leverage."""
        engine = _engine()
        result_a = engine.evaluate(
            _idea(target=Route.A),
            _portfolio(target=Route.A),
            MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        result_b = engine.evaluate(
            _idea(target=Route.B),
            _portfolio(target=Route.B),
            MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result_a.approved is True
        assert result_b.approved is True
        # A allows larger positions (40% equity) vs B (25%)
        assert result_a.proposed_order is not None
        assert result_b.proposed_order is not None
        assert result_a.proposed_order.size >= result_b.proposed_order.size


# ---------------------------------------------------------------------------
# Serialization Round-Trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_order_to_dict_fields(self) -> None:
        """order_to_dict should produce all required fields."""
        from agents.risk.main import order_to_dict

        engine = _engine()
        result = engine.evaluate(
            _idea(), _portfolio(), MARKET_PRICE, utc_now(), FUNDING_RATE,
        )
        assert result.proposed_order is not None

        d = order_to_dict(result.proposed_order)
        assert d["route"] == "autonomous"
        assert d["side"] == "BUY"
        assert d["instrument"] == TEST_INSTRUMENT_ID
        assert d["status"] == "risk_approved"
        assert Decimal(d["size"]) > 0
        assert Decimal(d["estimated_fee_usdc"]) > 0

    def test_deserialize_idea_roundtrip(self) -> None:
        """Serialized → deserialized idea should preserve fields."""
        from agents.risk.main import deserialize_idea

        payload = {
            "idea_id": "idea-abc",
            "timestamp": "2025-06-15T12:00:00+00:00",
            "instrument": TEST_INSTRUMENT_ID,
            "route": "autonomous",
            "direction": "LONG",
            "conviction": "0.75",
            "sources": "momentum,funding_arb",
            "time_horizon_seconds": "14400",
            "entry_price": "2000.00",
            "stop_loss": "1900.00",
            "take_profit": "2200.00",
            "reasoning": "Test",
        }
        idea = deserialize_idea(payload)
        assert idea.idea_id == "idea-abc"
        assert idea.route == Route.A
        assert idea.direction == PositionSide.LONG
        assert idea.conviction == 0.75
        assert idea.entry_price == Decimal("2000.00")
        assert len(idea.sources) == 2
        assert idea.time_horizon == timedelta(hours=4)
