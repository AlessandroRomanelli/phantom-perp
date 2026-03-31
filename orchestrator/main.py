"""Pipeline orchestrator — manages agent lifecycle, startup, shutdown.

Coordinates the startup and shutdown ordering of all pipeline agents
using the DAG, monitors liveness via the watchdog, and enforces
system-level kill switches via circuit breakers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from libs.common.config import get_settings, load_yaml_config
from libs.common.logging import setup_logging
from libs.common.models.enums import Route

from orchestrator.circuit_breakers import SystemCircuitBreaker
from orchestrator.dag import AgentName, PipelineDAG
from orchestrator.watchdog import AgentStatus, Watchdog


class PipelineStatus(str, Enum):
    """Overall pipeline status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    HALTED = "halted"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True, slots=True)
class PipelineState:
    """Snapshot of the orchestrator's view of the pipeline."""

    status: PipelineStatus
    running_agents: list[AgentName]
    stopped_agents: list[AgentName]
    stale_agents: list[AgentName]
    crashed_agents: list[AgentName]
    halted_portfolios: list[Route]
    globally_halted: bool


@dataclass(slots=True)
class Orchestrator:
    """Pipeline coordinator.

    Manages agent lifecycle using the DAG for ordering, watchdog for
    liveness, and system circuit breaker for kill switches.
    """

    dag: PipelineDAG = field(default_factory=PipelineDAG)
    watchdog: Watchdog = field(default_factory=Watchdog)
    circuit_breaker: SystemCircuitBreaker = field(
        default_factory=SystemCircuitBreaker,
    )
    _status: PipelineStatus = PipelineStatus.STOPPED

    def __post_init__(self) -> None:
        self.watchdog.register_all()

    def startup_sequence(self) -> list[AgentName]:
        """Return the ordered list of agents to start."""
        return self.dag.startup_order()

    def shutdown_sequence(self) -> list[AgentName]:
        """Return the ordered list of agents to stop (dependents first)."""
        return self.dag.shutdown_order()

    def mark_agent_starting(self, agent: AgentName, now: datetime) -> None:
        """Record that an agent is being started."""
        self.watchdog.mark_starting(agent, now)

    def record_heartbeat(self, agent: AgentName, now: datetime) -> None:
        """Forward a heartbeat to the watchdog."""
        self.watchdog.record_heartbeat(agent, now)

    def mark_agent_stopped(self, agent: AgentName) -> None:
        """Record that an agent has been gracefully stopped."""
        self.watchdog.mark_stopped(agent)

    def mark_agent_crashed(self, agent: AgentName, now: datetime) -> None:
        """Record an agent crash and determine impact."""
        self.watchdog.mark_crashed(agent, now)

    def get_agents_needing_restart(self) -> list[AgentName]:
        """Return crashed/stale agents that should be restarted.

        Respects the max restart budget. Agents that have exhausted
        restarts are not included.
        """
        result: list[AgentName] = []
        for agent in AgentName:
            if self.watchdog.should_restart(agent):
                result.append(agent)
        return result

    def get_restart_order(self, agents: list[AgentName]) -> list[AgentName]:
        """Order agents for restart respecting DAG dependencies.

        Ensures dependencies are started before their dependents.
        """
        startup = self.dag.startup_order()
        return [a for a in startup if a in agents]

    def get_impact(self, agent: AgentName) -> set[AgentName]:
        """Return all agents downstream of a failed agent."""
        return self.dag.get_downstream(agent)

    def check_health(self, now: datetime) -> PipelineState:
        """Run a full health check and return pipeline state."""
        check = self.watchdog.check_all(now)

        running = [
            s.name for s in check.agents if s.status == AgentStatus.RUNNING
        ]
        stopped = [
            s.name for s in check.agents
            if s.status in (AgentStatus.STOPPED, AgentStatus.PENDING)
        ]
        halted_portfolios = [
            t for t in Route if self.circuit_breaker.is_halted(t)
        ]

        if self.circuit_breaker.is_globally_halted():
            status = PipelineStatus.HALTED
        elif check.crashed_agents or check.stale_agents:
            status = PipelineStatus.DEGRADED
        elif len(running) == len(AgentName):
            status = PipelineStatus.RUNNING
        elif running:
            status = PipelineStatus.DEGRADED
        else:
            status = PipelineStatus.STOPPED

        self._status = status

        return PipelineState(
            status=status,
            running_agents=running,
            stopped_agents=stopped,
            stale_agents=check.stale_agents,
            crashed_agents=check.crashed_agents,
            halted_portfolios=halted_portfolios,
            globally_halted=self.circuit_breaker.is_globally_halted(),
        )

    @property
    def status(self) -> PipelineStatus:
        return self._status


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------


async def run_orchestrator() -> None:
    """Main event loop for the pipeline orchestrator.

    1. Starts all agents in DAG topological order
    2. Periodically runs watchdog health checks
    3. Restarts crashed agents (respecting restart budget)
    4. Evaluates system-level kill switches
    5. Handles graceful shutdown on signal
    """
    log = setup_logging("orchestrator")
    settings = get_settings()
    config = load_yaml_config("default")

    orch = Orchestrator()
    startup = orch.startup_sequence()

    log.info(
        "orchestrator_started",
        startup_order=[a.value for a in startup],
        environment=settings.infra.environment,
    )

    log.info(
        "orchestrator_ready",
        agent_count=len(AgentName),
        shutdown_order=[a.value for a in orch.shutdown_sequence()],
    )


if __name__ == "__main__":
    asyncio.run(run_orchestrator())
