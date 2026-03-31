"""Tests for orchestrator lifecycle management."""

from datetime import UTC, datetime, timedelta

from libs.common.models.enums import Route

from orchestrator.circuit_breakers import KillSwitchReason
from orchestrator.dag import AgentName
from orchestrator.main import Orchestrator, PipelineStatus
from orchestrator.watchdog import AgentStatus

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestOrchestrator:
    def test_initial_status(self) -> None:
        orch = Orchestrator()
        assert orch.status == PipelineStatus.STOPPED

    def test_startup_sequence(self) -> None:
        orch = Orchestrator()
        seq = orch.startup_sequence()
        assert seq[0] == AgentName.INGESTION
        assert len(seq) == len(AgentName)

    def test_shutdown_sequence(self) -> None:
        orch = Orchestrator()
        seq = orch.shutdown_sequence()
        assert seq[-1] == AgentName.INGESTION

    def test_all_agents_registered_on_init(self) -> None:
        orch = Orchestrator()
        assert len(orch.watchdog.registered_agents) == len(AgentName)


class TestHealthCheck:
    def test_all_running(self) -> None:
        orch = Orchestrator()
        for agent in AgentName:
            orch.mark_agent_starting(agent, T0)
            orch.record_heartbeat(agent, T0)
        state = orch.check_health(T0 + timedelta(seconds=5))
        assert state.status == PipelineStatus.RUNNING
        assert len(state.running_agents) == len(AgentName)
        assert state.stale_agents == []
        assert state.crashed_agents == []

    def test_degraded_on_crash(self) -> None:
        orch = Orchestrator()
        for agent in AgentName:
            orch.mark_agent_starting(agent, T0)
            orch.record_heartbeat(agent, T0)
        orch.mark_agent_crashed(AgentName.SIGNALS, T0)
        state = orch.check_health(T0 + timedelta(seconds=5))
        assert state.status == PipelineStatus.DEGRADED
        assert AgentName.SIGNALS in state.crashed_agents

    def test_halted_on_global_trip(self) -> None:
        orch = Orchestrator()
        for agent in AgentName:
            orch.mark_agent_starting(agent, T0)
            orch.record_heartbeat(agent, T0)
        orch.circuit_breaker.trip_global(
            KillSwitchReason.STALE_DATA, "test", T0,
        )
        state = orch.check_health(T0 + timedelta(seconds=5))
        assert state.status == PipelineStatus.HALTED
        assert state.globally_halted is True

    def test_halted_portfolios_listed(self) -> None:
        orch = Orchestrator()
        orch.circuit_breaker.trip_portfolio(
            Route.A, KillSwitchReason.DAILY_LOSS, "test", T0,
        )
        state = orch.check_health(T0)
        assert Route.A in state.halted_portfolios
        assert Route.B not in state.halted_portfolios

    def test_stopped_when_no_agents_running(self) -> None:
        orch = Orchestrator()
        state = orch.check_health(T0)
        assert state.status == PipelineStatus.STOPPED


class TestRestartManagement:
    def test_get_agents_needing_restart(self) -> None:
        orch = Orchestrator()
        orch.mark_agent_crashed(AgentName.SIGNALS, T0)
        needs = orch.get_agents_needing_restart()
        assert AgentName.SIGNALS in needs

    def test_restart_order_respects_dag(self) -> None:
        orch = Orchestrator()
        orch.mark_agent_crashed(AgentName.EXECUTION, T0)
        orch.mark_agent_crashed(AgentName.SIGNALS, T0)
        order = orch.get_restart_order([AgentName.EXECUTION, AgentName.SIGNALS])
        # Signals should come before Execution in restart order
        assert order.index(AgentName.SIGNALS) < order.index(AgentName.EXECUTION)

    def test_no_restart_when_running(self) -> None:
        orch = Orchestrator()
        orch.mark_agent_starting(AgentName.INGESTION, T0)
        orch.record_heartbeat(AgentName.INGESTION, T0)
        needs = orch.get_agents_needing_restart()
        assert AgentName.INGESTION not in needs


class TestImpactAnalysis:
    def test_ingestion_crash_impacts_all(self) -> None:
        orch = Orchestrator()
        impact = orch.get_impact(AgentName.INGESTION)
        assert len(impact) == len(AgentName) - 1

    def test_monitoring_crash_impacts_none(self) -> None:
        orch = Orchestrator()
        impact = orch.get_impact(AgentName.MONITORING)
        assert len(impact) == 0

    def test_execution_crash_impacts_downstream(self) -> None:
        orch = Orchestrator()
        impact = orch.get_impact(AgentName.EXECUTION)
        assert AgentName.RECONCILIATION in impact
        assert AgentName.MONITORING in impact
        assert AgentName.INGESTION not in impact
