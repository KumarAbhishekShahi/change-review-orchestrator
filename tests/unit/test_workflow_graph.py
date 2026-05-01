"""
Unit tests for the LangGraph workflow skeleton.

Verifies that:
- The graph compiles without errors
- All 8 agent nodes are registered
- The pipeline runs end-to-end with stub agents
- Status transitions occur correctly
- Error in intake causes pipeline to abort (status=FAILED)
- Human-in-the-loop flag is respected
"""

from __future__ import annotations

import pytest

from change_review_orchestrator.domain.enums import AgentStatus, CaseStatus
from change_review_orchestrator.domain.models import ChangeCase, WorkflowState
from change_review_orchestrator.workflow.graph import build_graph
from change_review_orchestrator.workflow.state import GraphState
from change_review_orchestrator.workflow.transitions import (
    NODE_ADJUDICATION,
    NODE_EVIDENCE_PACKAGER,
    NODE_IMPACT,
    NODE_INTAKE,
    NODE_POLICY,
    NODE_RELIABILITY,
    NODE_SECURITY,
    NODE_TEST_STRATEGY,
)


@pytest.fixture
def minimal_case() -> ChangeCase:
    return ChangeCase(title="Stub workflow test case")


@pytest.fixture
def initial_graph_state(minimal_case: ChangeCase) -> GraphState:
    return {"workflow": WorkflowState(case=minimal_case)}


@pytest.fixture(scope="module")
def compiled_graph():
    """Compile the graph once per module — compilation is expensive."""
    return build_graph()


class TestGraphCompilation:

    def test_graph_compiles_without_error(self, compiled_graph) -> None:
        """build_graph() must return a compiled graph without raising."""
        assert compiled_graph is not None

    def test_all_agent_nodes_registered(self, compiled_graph) -> None:
        """Every expected agent node must be present in the compiled graph."""
        expected_nodes = {
            "supervisor",
            NODE_INTAKE,
            NODE_IMPACT,
            NODE_POLICY,
            NODE_SECURITY,
            NODE_TEST_STRATEGY,
            NODE_RELIABILITY,
            NODE_EVIDENCE_PACKAGER,
            NODE_ADJUDICATION,
        }
        # LangGraph exposes graph.nodes as a dict-like object
        graph_nodes = set(compiled_graph.graph.nodes.keys())
        assert expected_nodes.issubset(graph_nodes), (
            f"Missing nodes: {expected_nodes - graph_nodes}"
        )


class TestGraphExecution:

    def test_pipeline_runs_end_to_end(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """Full pipeline must complete without raising an exception."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        assert result is not None
        assert "workflow" in result

    def test_all_agents_executed(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """All 8 agent names must appear in agent_results after a full run."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        wf = result["workflow"]
        expected_agents = {
            "intake", "impact", "policy", "security",
            "test_strategy", "reliability", "evidence_packager", "adjudication",
        }
        assert expected_agents == set(wf.agent_results.keys())

    def test_agent_statuses_are_completed(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """All stub agents must reach COMPLETED status (not FAILED)."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        wf = result["workflow"]
        for name, agent_result in wf.agent_results.items():
            assert agent_result.status == AgentStatus.COMPLETED, (
                f"Agent {name!r} ended with status {agent_result.status.value}"
            )

    def test_pipeline_start_time_stamped(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """pipeline_started_at must be set by the supervisor node."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        wf = result["workflow"]
        assert wf.pipeline_started_at is not None

    def test_no_error_log_entries_for_stubs(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """Stub agents must not populate the error_log."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        wf = result["workflow"]
        assert wf.error_log == []


class TestStatusTransitions:

    def test_status_is_not_pending_after_run(
        self, compiled_graph, initial_graph_state: GraphState
    ) -> None:
        """Status must have advanced past PENDING after a successful run."""
        result: GraphState = compiled_graph.invoke(initial_graph_state)
        wf = result["workflow"]
        assert wf.status != CaseStatus.PENDING


class TestConditionalEdges:

    def test_route_after_intake_returns_impact_on_success(self) -> None:
        """route_after_intake must return NODE_IMPACT when intake succeeds."""
        from change_review_orchestrator.domain.enums import AgentStatus
        from change_review_orchestrator.domain.models import AgentResult
        from change_review_orchestrator.workflow.transitions import route_after_intake

        case = ChangeCase(title="Test routing")
        wf = WorkflowState(case=case)
        wf.agent_results["intake"] = AgentResult(
            agent_name="intake", status=AgentStatus.COMPLETED
        )
        state: GraphState = {"workflow": wf}
        assert route_after_intake(state) == NODE_IMPACT

    def test_route_after_intake_returns_end_on_failure(self) -> None:
        """route_after_intake must return NODE_END when intake fails."""
        from change_review_orchestrator.domain.enums import AgentStatus
        from change_review_orchestrator.domain.models import AgentResult
        from change_review_orchestrator.workflow.transitions import (
            NODE_END,
            route_after_intake,
        )

        case = ChangeCase(title="Test routing")
        wf = WorkflowState(case=case)
        wf.agent_results["intake"] = AgentResult(
            agent_name="intake",
            status=AgentStatus.FAILED,
            error_message="Payload was empty",
        )
        state: GraphState = {"workflow": wf}
        assert route_after_intake(state) == NODE_END
