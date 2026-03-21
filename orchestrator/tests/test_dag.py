"""Tests for pipeline DAG definition and ordering."""

from orchestrator.dag import AgentName, PipelineDAG


class TestStartupOrder:
    def test_ingestion_starts_first(self) -> None:
        dag = PipelineDAG()
        order = dag.startup_order()
        assert order[0] == AgentName.INGESTION

    def test_all_agents_present(self) -> None:
        dag = PipelineDAG()
        order = dag.startup_order()
        assert set(order) == set(AgentName)
        assert len(order) == len(AgentName)

    def test_dependencies_before_dependents(self) -> None:
        dag = PipelineDAG()
        order = dag.startup_order()
        idx = {name: i for i, name in enumerate(order)}

        # Signals depends on Ingestion
        assert idx[AgentName.INGESTION] < idx[AgentName.SIGNALS]
        # Alpha depends on Signals
        assert idx[AgentName.SIGNALS] < idx[AgentName.ALPHA]
        # Risk depends on Alpha
        assert idx[AgentName.ALPHA] < idx[AgentName.RISK]
        # Confirmation depends on Risk
        assert idx[AgentName.RISK] < idx[AgentName.CONFIRMATION]
        # Execution depends on Risk and Confirmation
        assert idx[AgentName.RISK] < idx[AgentName.EXECUTION]
        assert idx[AgentName.CONFIRMATION] < idx[AgentName.EXECUTION]

    def test_is_deterministic(self) -> None:
        dag = PipelineDAG()
        order1 = dag.startup_order()
        order2 = dag.startup_order()
        assert order1 == order2


class TestShutdownOrder:
    def test_reverse_of_startup(self) -> None:
        dag = PipelineDAG()
        assert dag.shutdown_order() == list(reversed(dag.startup_order()))

    def test_monitoring_shuts_down_first(self) -> None:
        dag = PipelineDAG()
        shutdown = dag.shutdown_order()
        # Monitoring has no dependents → should be among first to stop
        monitoring_idx = shutdown.index(AgentName.MONITORING)
        ingestion_idx = shutdown.index(AgentName.INGESTION)
        assert monitoring_idx < ingestion_idx


class TestDependencies:
    def test_ingestion_has_no_deps(self) -> None:
        dag = PipelineDAG()
        assert dag.get_dependencies(AgentName.INGESTION) == []

    def test_signals_depends_on_ingestion(self) -> None:
        dag = PipelineDAG()
        assert AgentName.INGESTION in dag.get_dependencies(AgentName.SIGNALS)

    def test_execution_depends_on_risk_and_confirmation(self) -> None:
        dag = PipelineDAG()
        deps = dag.get_dependencies(AgentName.EXECUTION)
        assert AgentName.RISK in deps
        assert AgentName.CONFIRMATION in deps


class TestDependents:
    def test_ingestion_dependents(self) -> None:
        dag = PipelineDAG()
        dependents = dag.get_dependents(AgentName.INGESTION)
        assert AgentName.SIGNALS in dependents

    def test_risk_dependents(self) -> None:
        dag = PipelineDAG()
        dependents = dag.get_dependents(AgentName.RISK)
        assert AgentName.CONFIRMATION in dependents
        assert AgentName.EXECUTION in dependents

    def test_monitoring_has_no_dependents(self) -> None:
        dag = PipelineDAG()
        assert dag.get_dependents(AgentName.MONITORING) == []


class TestDownstream:
    def test_ingestion_affects_everything(self) -> None:
        dag = PipelineDAG()
        downstream = dag.get_downstream(AgentName.INGESTION)
        # Every agent except ingestion itself is downstream
        assert len(downstream) == len(AgentName) - 1
        assert AgentName.INGESTION not in downstream

    def test_monitoring_affects_nothing(self) -> None:
        dag = PipelineDAG()
        assert dag.get_downstream(AgentName.MONITORING) == set()

    def test_execution_downstream(self) -> None:
        dag = PipelineDAG()
        downstream = dag.get_downstream(AgentName.EXECUTION)
        assert AgentName.RECONCILIATION in downstream
        assert AgentName.MONITORING in downstream


class TestUpstream:
    def test_ingestion_has_no_upstream(self) -> None:
        dag = PipelineDAG()
        assert dag.get_upstream(AgentName.INGESTION) == set()

    def test_monitoring_upstream(self) -> None:
        dag = PipelineDAG()
        upstream = dag.get_upstream(AgentName.MONITORING)
        # Everything is upstream of monitoring
        assert AgentName.INGESTION in upstream
        assert AgentName.EXECUTION in upstream
        assert AgentName.RECONCILIATION in upstream


class TestAllAgents:
    def test_all_agents_listed(self) -> None:
        dag = PipelineDAG()
        assert len(dag.all_agents) == 8
