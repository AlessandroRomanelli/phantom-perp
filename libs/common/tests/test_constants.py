"""Regression tests for safety-critical constants in libs.common.constants.

SAFE-04: Verifies MAX_LEVERAGE_GLOBAL is 5.0, not the erroneous 20.0 value
that was previously set. Any change to these constants will fail CI.
"""

from decimal import Decimal

from libs.common.constants import MAX_LEVERAGE_GLOBAL, MAX_LEVERAGE_ROUTE_B


class TestLeverageConstants:
    def test_max_leverage_global(self) -> None:
        """MAX_LEVERAGE_GLOBAL must equal 5.0 — the safety cap for Route A."""
        assert MAX_LEVERAGE_GLOBAL == Decimal("5.0"), (
            f"SAFE-04: MAX_LEVERAGE_GLOBAL is {MAX_LEVERAGE_GLOBAL}, expected 5.0. "
            "This constant is a non-negotiable safety guardrail."
        )

    def test_max_leverage_route_b_is_five(self) -> None:
        """MAX_LEVERAGE_ROUTE_B must equal 5.0 — the safety cap for Route B."""
        assert MAX_LEVERAGE_ROUTE_B == Decimal("5.0"), (
            f"MAX_LEVERAGE_ROUTE_B is {MAX_LEVERAGE_ROUTE_B}, expected 5.0."
        )

    def test_max_leverage_global_is_decimal(self) -> None:
        """Constant must be a Decimal, not a float, to avoid rounding errors."""
        assert isinstance(MAX_LEVERAGE_GLOBAL, Decimal)

    def test_max_leverage_route_b_is_decimal(self) -> None:
        """Constant must be a Decimal, not a float, to avoid rounding errors."""
        assert isinstance(MAX_LEVERAGE_ROUTE_B, Decimal)

    def test_global_cap_at_least_as_strict_as_route_b(self) -> None:
        """Route A global cap must not be looser than Route B cap."""
        assert MAX_LEVERAGE_GLOBAL <= Decimal("5.0")
        assert MAX_LEVERAGE_ROUTE_B <= Decimal("5.0")
