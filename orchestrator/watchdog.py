"""Agent liveness watchdog — detects crashed or stalled agents.

The watchdog tracks heartbeats from each agent and determines which
agents need attention (restart, alert, or escalation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from orchestrator.dag import AgentName


class AgentStatus(str, Enum):
    """Runtime status of a managed agent process."""

    PENDING = "pending"       # Not yet started
    STARTING = "starting"     # Start requested, awaiting first heartbeat
    RUNNING = "running"       # Healthy and sending heartbeats
    STALE = "stale"           # Heartbeat overdue
    STOPPED = "stopped"       # Gracefully stopped
    CRASHED = "crashed"       # Confirmed dead
    RESTARTING = "restarting"  # Restart in progress


@dataclass(slots=True)
class AgentState:
    """Tracked state of a single agent."""

    name: AgentName
    status: AgentStatus = AgentStatus.PENDING
    last_heartbeat: datetime | None = None
    start_count: int = 0
    crash_count: int = 0
    last_started_at: datetime | None = None
    last_crashed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WatchdogCheck:
    """Result of a watchdog health check cycle."""

    timestamp: datetime
    agents: list[AgentState]
    stale_agents: list[AgentName]
    crashed_agents: list[AgentName]
    all_healthy: bool


@dataclass(slots=True)
class Watchdog:
    """Monitors agent liveness and tracks restart history.

    The orchestrator main loop calls check_all() periodically to
    detect agents that have gone silent.
    """

    heartbeat_timeout: timedelta = field(
        default_factory=lambda: timedelta(seconds=60),
    )
    max_restarts: int = 5
    _agents: dict[AgentName, AgentState] = field(default_factory=dict)

    def register(self, agent: AgentName) -> None:
        """Register an agent for monitoring."""
        if agent not in self._agents:
            self._agents[agent] = AgentState(name=agent)

    def register_all(self) -> None:
        """Register all pipeline agents."""
        for agent in AgentName:
            self.register(agent)

    def mark_starting(self, agent: AgentName, now: datetime) -> None:
        """Mark an agent as starting up."""
        state = self._agents[agent]
        state.status = AgentStatus.STARTING
        state.start_count += 1
        state.last_started_at = now

    def record_heartbeat(self, agent: AgentName, now: datetime) -> None:
        """Record a heartbeat — transitions STARTING/STALE → RUNNING."""
        state = self._agents[agent]
        state.last_heartbeat = now
        if state.status in (AgentStatus.STARTING, AgentStatus.STALE, AgentStatus.RESTARTING):
            state.status = AgentStatus.RUNNING

    def mark_stopped(self, agent: AgentName) -> None:
        """Mark an agent as gracefully stopped."""
        self._agents[agent].status = AgentStatus.STOPPED

    def mark_crashed(self, agent: AgentName, now: datetime) -> None:
        """Mark an agent as crashed."""
        state = self._agents[agent]
        state.status = AgentStatus.CRASHED
        state.crash_count += 1
        state.last_crashed_at = now

    def mark_restarting(self, agent: AgentName, now: datetime) -> None:
        """Mark an agent as being restarted."""
        state = self._agents[agent]
        state.status = AgentStatus.RESTARTING
        state.start_count += 1
        state.last_started_at = now

    def should_restart(self, agent: AgentName) -> bool:
        """Whether an agent should be restarted (under max restart limit)."""
        state = self._agents[agent]
        if state.status not in (AgentStatus.CRASHED, AgentStatus.STALE):
            return False
        return state.crash_count < self.max_restarts

    def restart_budget_exhausted(self, agent: AgentName) -> bool:
        """True if the agent has exceeded its restart budget."""
        return self._agents[agent].crash_count >= self.max_restarts

    def check_all(self, now: datetime) -> WatchdogCheck:
        """Run a health check on all registered agents.

        Transitions RUNNING agents to STALE if heartbeat is overdue.
        """
        stale: list[AgentName] = []
        crashed: list[AgentName] = []

        for name, state in self._agents.items():
            if state.status == AgentStatus.RUNNING and state.last_heartbeat:
                age = now - state.last_heartbeat
                if age > self.heartbeat_timeout:
                    state.status = AgentStatus.STALE
                    stale.append(name)

            if state.status == AgentStatus.STALE:
                stale.append(name) if name not in stale else None
            elif state.status == AgentStatus.CRASHED:
                crashed.append(name)

        return WatchdogCheck(
            timestamp=now,
            agents=list(self._agents.values()),
            stale_agents=stale,
            crashed_agents=crashed,
            all_healthy=len(stale) == 0 and len(crashed) == 0,
        )

    def get_status(self, agent: AgentName) -> AgentStatus:
        """Get current status of an agent."""
        return self._agents[agent].status

    def get_state(self, agent: AgentName) -> AgentState:
        """Get full state of an agent."""
        return self._agents[agent]

    @property
    def registered_agents(self) -> list[AgentName]:
        return sorted(self._agents.keys(), key=lambda a: a.value)
