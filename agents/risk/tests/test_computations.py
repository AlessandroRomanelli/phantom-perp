"""Tests for risk computation modules: margin, fees, funding, sizing, liquidation."""

from datetime import timedelta
from decimal import Decimal

import pytest

from libs.common.constants import FEE_MAKER, FEE_TAKER
from libs.common.models.enums import Route, PositionSide
from libs.common.models.position import PerpPosition

from agents.risk.fee_calculator import estimate_fee
from agents.risk.funding_cost_estimator import estimate_funding_cost
from agents.risk.limits import RiskLimits
from agents.risk.liquidation_guard import stop_is_before_liquidation
from agents.risk.margin_calculator import (
    compute_initial_margin,
    compute_liquidation_distance_pct,
    compute_liquidation_price,
    compute_maintenance_margin,
)
from agents.risk.position_sizer import compute_position_size


def _default_limits(**overrides: object) -> RiskLimits:
    defaults = dict(
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
        conviction_power=2.0,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)  # type: ignore[arg-type]


def _make_position(
    size: Decimal = Decimal("1"),
    mark: Decimal = Decimal("2000"),
    side: PositionSide = PositionSide.LONG,
) -> PerpPosition:
    return PerpPosition(
        instrument="ETH-PERP",
        route=Route.A,
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


# ── Margin Calculator ────────────────────────────────────────────────────


class TestInitialMargin:
    def test_basic_calculation(self) -> None:
        margin = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("5"))
        assert margin == Decimal("400")

    def test_higher_leverage_lower_margin(self) -> None:
        m5 = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("5"))
        m3 = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("3"))
        assert m5 < m3

    def test_zero_leverage_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("0"))


class TestMaintenanceMargin:
    def test_default_rate(self) -> None:
        maint = compute_maintenance_margin(Decimal("1"), Decimal("2000"))
        assert maint == Decimal("20")  # 1 * 2000 * 0.01

    def test_custom_rate(self) -> None:
        maint = compute_maintenance_margin(
            Decimal("1"), Decimal("2000"), rate=Decimal("0.005"),
        )
        assert maint == Decimal("10")


class TestLiquidationPrice:
    def test_long_liq_below_entry(self) -> None:
        liq = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.LONG)
        assert liq < Decimal("2000")

    def test_short_liq_above_entry(self) -> None:
        liq = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.SHORT)
        assert liq > Decimal("2000")

    def test_higher_leverage_closer_liquidation_long(self) -> None:
        liq_3x = compute_liquidation_price(Decimal("2000"), Decimal("3"), PositionSide.LONG)
        liq_5x = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.LONG)
        assert liq_5x > liq_3x  # 5x liq is closer to entry (higher price)

    def test_higher_leverage_closer_liquidation_short(self) -> None:
        liq_3x = compute_liquidation_price(Decimal("2000"), Decimal("3"), PositionSide.SHORT)
        liq_5x = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.SHORT)
        assert liq_5x < liq_3x  # 5x liq is closer to entry (lower price)


class TestLiquidationDistance:
    def test_long_distance(self) -> None:
        dist = compute_liquidation_distance_pct(
            Decimal("2000"), Decimal("1600"), PositionSide.LONG,
        )
        assert dist == Decimal("20.00")

    def test_short_distance(self) -> None:
        dist = compute_liquidation_distance_pct(
            Decimal("2000"), Decimal("2400"), PositionSide.SHORT,
        )
        assert dist == Decimal("20.00")


# ── Fee Calculator ───────────────────────────────────────────────────────


class TestFeeEstimation:
    def test_maker_fee(self) -> None:
        fee = estimate_fee(Decimal("1"), Decimal("2000"), is_maker=True)
        assert fee == Decimal("0.25")  # 2000 * 0.000125

    def test_taker_fee(self) -> None:
        fee = estimate_fee(Decimal("1"), Decimal("2000"), is_maker=False)
        assert fee == Decimal("0.50")  # 2000 * 0.000250

    def test_maker_cheaper_than_taker(self) -> None:
        maker = estimate_fee(Decimal("1"), Decimal("2000"), is_maker=True)
        taker = estimate_fee(Decimal("1"), Decimal("2000"), is_maker=False)
        assert maker < taker


# ── Funding Cost Estimator ───────────────────────────────────────────────


class TestFundingCost:
    def test_long_positive_rate_is_paying(self) -> None:
        """Positive funding: longs pay shorts → LONG pays."""
        est = estimate_funding_cost(
            Decimal("1"), Decimal("2000"), Decimal("0.0001"),
            PositionSide.LONG, timedelta(hours=4),
        )
        assert est.is_paying is True
        assert est.hourly_cost_usdc < 0

    def test_short_positive_rate_receives(self) -> None:
        """Positive funding: longs pay shorts → SHORT receives."""
        est = estimate_funding_cost(
            Decimal("1"), Decimal("2000"), Decimal("0.0001"),
            PositionSide.SHORT, timedelta(hours=4),
        )
        assert est.is_paying is False
        assert est.hourly_cost_usdc > 0

    def test_total_scales_with_holding_period(self) -> None:
        est_4h = estimate_funding_cost(
            Decimal("1"), Decimal("2000"), Decimal("0.0001"),
            PositionSide.LONG, timedelta(hours=4),
        )
        est_8h = estimate_funding_cost(
            Decimal("1"), Decimal("2000"), Decimal("0.0001"),
            PositionSide.LONG, timedelta(hours=8),
        )
        # 8h cost should be double 4h cost
        assert abs(est_8h.total_cost_usdc) == abs(est_4h.total_cost_usdc) * 2

    def test_daily_cost(self) -> None:
        est = estimate_funding_cost(
            Decimal("1"), Decimal("2000"), Decimal("0.0001"),
            PositionSide.LONG, timedelta(hours=24),
        )
        # Daily cost = hourly * 24
        assert est.daily_cost_usdc == est.hourly_cost_usdc * 24


# ── Liquidation Guard ────────────────────────────────────────────────────


class TestLiquidationGuard:
    def test_long_stop_above_liq_is_safe(self) -> None:
        assert stop_is_before_liquidation(
            Decimal("1900"), Decimal("1600"), PositionSide.LONG,
        ) is True

    def test_long_stop_below_liq_is_unsafe(self) -> None:
        assert stop_is_before_liquidation(
            Decimal("1500"), Decimal("1600"), PositionSide.LONG,
        ) is False

    def test_short_stop_below_liq_is_safe(self) -> None:
        assert stop_is_before_liquidation(
            Decimal("2300"), Decimal("2400"), PositionSide.SHORT,
        ) is True

    def test_short_stop_above_liq_is_unsafe(self) -> None:
        assert stop_is_before_liquidation(
            Decimal("2500"), Decimal("2400"), PositionSide.SHORT,
        ) is False


# ── Position Sizer ───────────────────────────────────────────────────────


class TestPositionSizer:
    def test_basic_sizing(self) -> None:
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(),
        )
        assert size > Decimal("0")
        assert size * Decimal("2000") <= Decimal("6000")  # max_position_notional_usdc

    def test_conviction_scales_size(self) -> None:
        full = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(),
        )
        half = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=0.5,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(),
        )
        assert half < full
        assert half > Decimal("0")

    def test_respects_max_notional(self) -> None:
        """Even with huge equity, notional is capped by max_position_notional_usdc."""
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("1000000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(max_position_notional_usdc=Decimal("4000")),
        )
        assert size <= Decimal("2")  # 4000 / 2000

    def test_respects_max_pct_equity(self) -> None:
        """Position margin is capped at max_position_pct_equity of equity, notional scales with leverage."""
        limits = _default_limits(
            max_position_pct_equity=Decimal("10"),
            max_position_notional_usdc=Decimal("200000"),  # Not binding
        )
        # max_leverage default is 5x; max margin = 10% of 10000 = 1000; max notional = 1000 * 5 = 5000
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )
        # Notional = size * price; margin = notional / leverage
        notional = size * Decimal("2000")
        margin = notional / limits.max_leverage
        assert margin <= Decimal("1000")  # margin ≤ 10% of equity

    def test_existing_positions_reduce_available(self) -> None:
        """Existing positions consume leverage budget, reducing available size."""
        # Set equity-pct very high so it is NOT the binding constraint;
        # leverage and margin utilization become the binding constraints instead.
        limits = _default_limits(
            max_position_pct_equity=Decimal("10000"),
            max_position_notional_usdc=Decimal("200000"),
        )
        pos = _make_position(size=Decimal("2"), mark=Decimal("2000"))
        size_with = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("2000"),
            used_margin=Decimal("800"),
            existing_positions=[pos],
            limits=limits,
        )
        size_without = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("2000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )
        assert size_with < size_without

    def test_zero_equity_returns_zero(self) -> None:
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("0"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(),
        )
        assert size == Decimal("0")

    def test_size_rounded_to_valid_increment(self) -> None:
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=0.73,
            equity=Decimal("5000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=_default_limits(),
        )
        # Must be a multiple of min_order_size
        assert size % Decimal("0.0001") == 0


# ── Convex Sizing ────────────────────────────────────────────────────────


class TestConvexSizing:
    """Verify that conviction_power produces non-linear position scaling."""

    def _size_at(self, conviction: float, power: float = 2.0) -> Decimal:
        limits = _default_limits(conviction_power=power)
        return compute_position_size(
            entry_price=Decimal("2000"),
            conviction=conviction,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )

    def test_convex_scaling_09_conviction(self) -> None:
        """conviction=0.9 with power=2 → size ≈ 81% of conviction=1.0 size."""
        size_full = self._size_at(1.0)
        size_09 = self._size_at(0.9)
        ratio = float(size_09 / size_full)
        assert abs(ratio - 0.81) < 0.01, f"Expected ~0.81, got {ratio:.4f}"

    def test_convex_scaling_05_conviction(self) -> None:
        """conviction=0.5 with power=2 → size ≈ 25% of conviction=1.0 size."""
        size_full = self._size_at(1.0)
        size_05 = self._size_at(0.5)
        ratio = float(size_05 / size_full)
        assert abs(ratio - 0.25) < 0.01, f"Expected ~0.25, got {ratio:.4f}"

    def test_linear_fallback_power_one(self) -> None:
        """With conviction_power=1.0, conviction=0.5 → 50% of max size."""
        size_full = self._size_at(1.0, power=1.0)
        size_half = self._size_at(0.5, power=1.0)
        ratio = float(size_half / size_full)
        assert abs(ratio - 0.50) < 0.01, f"Expected ~0.50, got {ratio:.4f}"

    def test_zero_conviction_returns_zero(self) -> None:
        """conviction=0 must always produce size=0."""
        size = self._size_at(0.0)
        assert size == Decimal("0")


# ── Route A vs Route B Margin Differentiation ───────────────────────────


class TestRouteABMargin:
    """Verify margin, liquidation, and distance differences between Route A (10x) and B (5x)."""

    def test_route_a_initial_margin_10x(self) -> None:
        margin = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("10"))
        assert margin == Decimal("200")

    def test_route_b_initial_margin_5x(self) -> None:
        margin = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("5"))
        assert margin == Decimal("400")

    def test_route_a_less_margin_than_b(self) -> None:
        margin_a = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("10"))
        margin_b = compute_initial_margin(Decimal("1"), Decimal("2000"), Decimal("5"))
        assert margin_a < margin_b

    def test_route_a_liq_closer_than_b_long(self) -> None:
        """10x liquidation price is closer to entry (higher) than 5x for LONG."""
        liq_10x = compute_liquidation_price(Decimal("2000"), Decimal("10"), PositionSide.LONG)
        liq_5x = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.LONG)
        assert liq_10x > liq_5x

    def test_route_a_liq_closer_than_b_short(self) -> None:
        """10x liquidation price is closer to entry (lower) than 5x for SHORT."""
        liq_10x = compute_liquidation_price(Decimal("2000"), Decimal("10"), PositionSide.SHORT)
        liq_5x = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.SHORT)
        assert liq_10x < liq_5x

    def test_route_a_liq_distance_narrower(self) -> None:
        """Distance to liquidation at 10x is smaller than at 5x."""
        liq_10x = compute_liquidation_price(Decimal("2000"), Decimal("10"), PositionSide.LONG)
        liq_5x = compute_liquidation_price(Decimal("2000"), Decimal("5"), PositionSide.LONG)
        dist_10x = compute_liquidation_distance_pct(
            Decimal("2000"), liq_10x, PositionSide.LONG,
        )
        dist_5x = compute_liquidation_distance_pct(
            Decimal("2000"), liq_5x, PositionSide.LONG,
        )
        assert dist_10x < dist_5x


# ── Liquidation Distance Threshold ──────────────────────────────────────


class TestLiquidationDistanceThreshold:
    """Verify stop_is_before_liquidation rejection and acceptance."""

    def test_rejects_position_too_close(self) -> None:
        """Stop at liquidation price → False (not strictly above for LONG)."""
        assert stop_is_before_liquidation(
            Decimal("1600"), Decimal("1600"), PositionSide.LONG,
        ) is False

    def test_accepts_safely_distant_long(self) -> None:
        """Stop above liquidation price → True for LONG."""
        assert stop_is_before_liquidation(
            Decimal("1800"), Decimal("1600"), PositionSide.LONG,
        ) is True

    def test_rejects_stop_beyond_liq_short(self) -> None:
        """Stop above liquidation price for SHORT → False."""
        assert stop_is_before_liquidation(
            Decimal("2500"), Decimal("2400"), PositionSide.SHORT,
        ) is False

    def test_accepts_safely_distant_short(self) -> None:
        """Stop below liquidation price → True for SHORT."""
        assert stop_is_before_liquidation(
            Decimal("2200"), Decimal("2400"), PositionSide.SHORT,
        ) is True

    def test_flat_direction_raises(self) -> None:
        """FLAT direction must raise ValueError."""
        with pytest.raises(ValueError):
            stop_is_before_liquidation(
                Decimal("1800"), Decimal("1600"), PositionSide.FLAT,
            )


# ── Position Sizer Max-Equity Bound ─────────────────────────────────────


class TestPositionSizerMaxEquityBound:
    """Verify position sizer respects equity percentage and margin caps."""

    def test_route_a_40pct_bound(self) -> None:
        """With 40% equity limit and 10x leverage, margin ≤ 40% of equity."""
        limits = _default_limits(
            max_leverage=Decimal("10"),
            max_position_pct_equity=Decimal("40"),
            max_position_notional_usdc=Decimal("200000"),
        )
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )
        notional = size * Decimal("2000")
        margin = notional / Decimal("10")
        assert margin <= Decimal("4000")  # 40% of 10000

    def test_route_b_25pct_bound(self) -> None:
        """With 25% equity limit and 5x leverage, margin ≤ 25% of equity."""
        limits = _default_limits(
            max_leverage=Decimal("5"),
            max_position_pct_equity=Decimal("25"),
            max_position_notional_usdc=Decimal("200000"),
        )
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )
        notional = size * Decimal("2000")
        margin = notional / Decimal("5")
        assert margin <= Decimal("2500")  # 25% of 10000

    def test_high_conviction_still_bounded(self) -> None:
        """At conviction=1.0, notional still ≤ max_position_notional_usdc."""
        limits = _default_limits(max_position_notional_usdc=Decimal("5000"))
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("1000000"),
            used_margin=Decimal("0"),
            existing_positions=[],
            limits=limits,
        )
        notional = size * Decimal("2000")
        assert notional <= Decimal("5000")

    def test_margin_utilization_caps(self) -> None:
        """With 6000 used margin out of 7000 max, remaining budget is limited."""
        limits = _default_limits(
            max_margin_utilization_pct=Decimal("70"),
            max_position_notional_usdc=Decimal("200000"),
            max_position_pct_equity=Decimal("10000"),
        )
        size = compute_position_size(
            entry_price=Decimal("2000"),
            conviction=1.0,
            equity=Decimal("10000"),
            used_margin=Decimal("6000"),
            existing_positions=[],
            limits=limits,
        )
        # max total margin = 70% of 10000 = 7000; available = 7000 - 6000 = 1000
        # max notional from margin = 1000 * 5 = 5000; max size = 5000 / 2000 = 2.5
        notional = size * Decimal("2000")
        margin_used = notional / Decimal("5")
        assert margin_used <= Decimal("1000")
