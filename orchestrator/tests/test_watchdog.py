"""Tests for agent liveness watchdog."""

from datetime import UTC, datetime, timedelta

from orchestrator.dag import AgentName
from orchestrator.watchdog import AgentStatus, Watchdog

T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


class TestRegistration:
    def test_register_agent(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        assert AgentName.INGESTION in wd.registered_agents

    def test_register_all(self) -> None:
        wd = Watchdog()
        wd.register_all()
        assert len(wd.registered_agents) == len(AgentName)

    def test_initial_status_pending(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.PENDING

    def test_duplicate_register_ignored(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.register(AgentName.INGESTION)
        # Status should still be starting (not reset to pending)
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.STARTING


class TestLifecycle:
    def test_starting(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.STARTING
        assert wd.get_state(AgentName.INGESTION).start_count == 1

    def test_heartbeat_transitions_to_running(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0 + timedelta(seconds=5))
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.RUNNING

    def test_stopped(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        wd.mark_stopped(AgentName.INGESTION)
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.STOPPED

    def test_crashed(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_crashed(AgentName.INGESTION, T0)
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.CRASHED
        assert wd.get_state(AgentName.INGESTION).crash_count == 1

    def test_restarting(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_crashed(AgentName.INGESTION, T0)
        wd.mark_restarting(AgentName.INGESTION, T0 + timedelta(seconds=5))
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.RESTARTING
        assert wd.get_state(AgentName.INGESTION).start_count == 1

    def test_restarting_transitions_to_running_on_heartbeat(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_restarting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0 + timedelta(seconds=5))
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.RUNNING


class TestStalenessDetection:
    def test_running_goes_stale(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60))
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        check = wd.check_all(T0 + timedelta(seconds=120))
        assert AgentName.INGESTION in check.stale_agents
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.STALE

    def test_recent_heartbeat_stays_healthy(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60))
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        check = wd.check_all(T0 + timedelta(seconds=30))
        assert check.all_healthy is True
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.RUNNING

    def test_stale_recovers_on_heartbeat(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60))
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        wd.check_all(T0 + timedelta(seconds=120))  # goes stale
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.STALE
        wd.record_heartbeat(AgentName.INGESTION, T0 + timedelta(seconds=130))
        assert wd.get_status(AgentName.INGESTION) == AgentStatus.RUNNING


class TestCheckAll:
    def test_all_healthy(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60))
        wd.register_all()
        for agent in AgentName:
            wd.mark_starting(agent, T0)
            wd.record_heartbeat(agent, T0)
        check = wd.check_all(T0 + timedelta(seconds=10))
        assert check.all_healthy is True
        assert check.stale_agents == []
        assert check.crashed_agents == []

    def test_mixed_health(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60))
        wd.register(AgentName.INGESTION)
        wd.register(AgentName.SIGNALS)
        wd.register(AgentName.ALPHA)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        wd.mark_starting(AgentName.SIGNALS, T0)
        wd.record_heartbeat(AgentName.SIGNALS, T0)
        wd.mark_crashed(AgentName.ALPHA, T0)

        check = wd.check_all(T0 + timedelta(seconds=10))
        assert check.all_healthy is False
        assert AgentName.ALPHA in check.crashed_agents


class TestRestartBudget:
    def test_should_restart_after_crash(self) -> None:
        wd = Watchdog(max_restarts=5)
        wd.register(AgentName.INGESTION)
        wd.mark_crashed(AgentName.INGESTION, T0)
        assert wd.should_restart(AgentName.INGESTION) is True

    def test_budget_exhausted(self) -> None:
        wd = Watchdog(max_restarts=2)
        wd.register(AgentName.INGESTION)
        wd.mark_crashed(AgentName.INGESTION, T0)
        wd.mark_crashed(AgentName.INGESTION, T0 + timedelta(seconds=10))
        assert wd.should_restart(AgentName.INGESTION) is False
        assert wd.restart_budget_exhausted(AgentName.INGESTION) is True

    def test_running_should_not_restart(self) -> None:
        wd = Watchdog()
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        assert wd.should_restart(AgentName.INGESTION) is False

    def test_stale_should_restart(self) -> None:
        wd = Watchdog(heartbeat_timeout=timedelta(seconds=60), max_restarts=5)
        wd.register(AgentName.INGESTION)
        wd.mark_starting(AgentName.INGESTION, T0)
        wd.record_heartbeat(AgentName.INGESTION, T0)
        wd.check_all(T0 + timedelta(seconds=120))  # goes stale
        assert wd.should_restart(AgentName.INGESTION) is True
