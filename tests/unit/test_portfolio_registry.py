"""Tests for the simplified portfolio target registry.

After removal of portfolio UUIDs from internal models, the registry
just re-exports Route. These tests verify the enum values
are correct and stable.
"""

from libs.common.models.enums import Route
from libs.portfolio.registry import Route as ReexportedTarget


class TestRouteEnum:
    def test_autonomous_value(self) -> None:
        assert Route.A.value == "autonomous"

    def test_user_confirmed_value(self) -> None:
        assert Route.B.value == "user_confirmed"

    def test_enum_has_exactly_two_members(self) -> None:
        assert len(Route) == 2

    def test_registry_reexports_same_type(self) -> None:
        """The registry module should re-export the same Route."""
        assert ReexportedTarget is Route

    def test_a_and_b_are_distinct(self) -> None:
        assert Route.A != Route.B

    def test_construct_from_value(self) -> None:
        assert Route("autonomous") == Route.A
        assert Route("user_confirmed") == Route.B
