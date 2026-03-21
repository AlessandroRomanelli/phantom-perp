"""Tests for the simplified portfolio target registry.

After removal of portfolio UUIDs from internal models, the registry
just re-exports PortfolioTarget. These tests verify the enum values
are correct and stable.
"""

from libs.common.models.enums import PortfolioTarget
from libs.portfolio.registry import PortfolioTarget as ReexportedTarget


class TestPortfolioTargetEnum:
    def test_autonomous_value(self) -> None:
        assert PortfolioTarget.A.value == "autonomous"

    def test_user_confirmed_value(self) -> None:
        assert PortfolioTarget.B.value == "user_confirmed"

    def test_enum_has_exactly_two_members(self) -> None:
        assert len(PortfolioTarget) == 2

    def test_registry_reexports_same_type(self) -> None:
        """The registry module should re-export the same PortfolioTarget."""
        assert ReexportedTarget is PortfolioTarget

    def test_a_and_b_are_distinct(self) -> None:
        assert PortfolioTarget.A != PortfolioTarget.B

    def test_construct_from_value(self) -> None:
        assert PortfolioTarget("autonomous") == PortfolioTarget.A
        assert PortfolioTarget("user_confirmed") == PortfolioTarget.B
