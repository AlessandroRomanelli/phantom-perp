"""Pipeline DAG definition and execution order.

Defines the dependency graph between agents and provides topological
ordering for startup sequencing and failure impact analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentName(str, Enum):
    """Canonical names for all pipeline agents."""

    INGESTION = "ingestion"
    SIGNALS = "signals"
    ALPHA = "alpha"
    RISK = "risk"
    CONFIRMATION = "confirmation"
    EXECUTION = "execution"
    RECONCILIATION = "reconciliation"
    MONITORING = "monitoring"


# ── Static dependency graph ─────────────────────────────────────────────
# Each agent lists the agents it depends on (must be running first).

AGENT_DEPENDENCIES: dict[AgentName, list[AgentName]] = {
    AgentName.INGESTION: [],
    AgentName.SIGNALS: [AgentName.INGESTION],
    AgentName.ALPHA: [AgentName.SIGNALS],
    AgentName.RISK: [AgentName.ALPHA],
    AgentName.CONFIRMATION: [AgentName.RISK],
    AgentName.EXECUTION: [AgentName.RISK, AgentName.CONFIRMATION],
    AgentName.RECONCILIATION: [AgentName.EXECUTION],
    AgentName.MONITORING: [AgentName.RECONCILIATION],
}
# Note: Risk also consumes stream:portfolio_state from Reconciliation
# at runtime, but this is not a startup dependency — Risk can operate
# with empty/default portfolio state until Reconciliation publishes.


@dataclass(frozen=True, slots=True)
class AgentNode:
    """A node in the pipeline DAG."""

    name: AgentName
    dependencies: list[AgentName]
    dependents: list[AgentName]


@dataclass(slots=True)
class PipelineDAG:
    """Directed acyclic graph of agent dependencies.

    Provides startup ordering and failure impact analysis.
    """

    _nodes: dict[AgentName, AgentNode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._nodes:
            self._build_from_defaults()

    def _build_from_defaults(self) -> None:
        # First pass: compute dependents (reverse edges)
        dependents_map: dict[AgentName, list[AgentName]] = {
            name: [] for name in AgentName
        }
        for name, deps in AGENT_DEPENDENCIES.items():
            for dep in deps:
                dependents_map[dep].append(name)

        for name in AgentName:
            self._nodes[name] = AgentNode(
                name=name,
                dependencies=list(AGENT_DEPENDENCIES.get(name, [])),
                dependents=dependents_map[name],
            )

    def startup_order(self) -> list[AgentName]:
        """Return agents in topological order (dependencies first).

        Uses Kahn's algorithm for deterministic ordering.
        """
        in_degree: dict[AgentName, int] = {
            name: len(node.dependencies) for name, node in self._nodes.items()
        }
        queue = sorted(
            [name for name, deg in in_degree.items() if deg == 0],
            key=lambda n: n.value,
        )
        order: list[AgentName] = []

        while queue:
            current = queue.pop(0)
            order.append(current)
            for dependent in sorted(
                self._nodes[current].dependents, key=lambda n: n.value,
            ):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return order

    def shutdown_order(self) -> list[AgentName]:
        """Return agents in reverse topological order (dependents first)."""
        return list(reversed(self.startup_order()))

    def get_dependencies(self, agent: AgentName) -> list[AgentName]:
        """Return direct dependencies for an agent."""
        return list(self._nodes[agent].dependencies)

    def get_dependents(self, agent: AgentName) -> list[AgentName]:
        """Return agents that directly depend on this agent."""
        return list(self._nodes[agent].dependents)

    def get_downstream(self, agent: AgentName) -> set[AgentName]:
        """Return all agents transitively downstream of this agent.

        Used for failure impact analysis: if this agent crashes,
        which other agents are affected?
        """
        visited: set[AgentName] = set()
        stack = list(self._nodes[agent].dependents)
        while stack:
            current = stack.pop()
            if current not in visited:
                visited.add(current)
                stack.extend(self._nodes[current].dependents)
        return visited

    def get_upstream(self, agent: AgentName) -> set[AgentName]:
        """Return all agents transitively upstream of this agent."""
        visited: set[AgentName] = set()
        stack = list(self._nodes[agent].dependencies)
        while stack:
            current = stack.pop()
            if current not in visited:
                visited.add(current)
                stack.extend(self._nodes[current].dependencies)
        return visited

    @property
    def all_agents(self) -> list[AgentName]:
        """All agents in the pipeline."""
        return list(AgentName)
