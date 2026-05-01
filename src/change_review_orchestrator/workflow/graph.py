"""
LangGraph StateGraph assembly for Change Review Orchestrator.

This module is the single place where all nodes and edges are declared.
Importing this module does NOT execute the graph — call build_graph()
to get a compiled CompiledGraph instance.

Graph topology:
  [START]
     └─> supervisor
           └─> intake              (conditional: FAIL → END)
                 └─> impact
                       └─> policy      ─┐
                           security     ├── (fan-out via Send in future; sequential for now)
                           test_strategy│
                           reliability  ┘
                                 └─> evidence_packager
                                           └─> adjudication
                                                 └─> (conditional: ESCALATED → human_review | END)
                                                           └─> [END]

Human-in-the-loop checkpoint is implemented as a passthrough node that
can be intercepted by LangGraph's interrupt mechanism when
ENABLE_HUMAN_IN_THE_LOOP=true (wired in Step 11).
"""

from __future__ import annotations

import structlog
from langgraph.graph import END, START, StateGraph

from change_review_orchestrator.agents import (
    AdjudicationAgent,
    EvidencePackagerAgent,
    ImpactAgent,
    IntakeAgent,
    PolicyAgent,
    ReliabilityAgent,
    SecurityAgent,
    TestStrategyAgent,
)
from change_review_orchestrator.domain.enums import CaseStatus
from change_review_orchestrator.workflow.state import GraphState
from change_review_orchestrator.workflow.supervisor import supervisor_node
from change_review_orchestrator.workflow.transitions import (
    NODE_ADJUDICATION,
    NODE_END,
    NODE_EVIDENCE_PACKAGER,
    NODE_HUMAN_REVIEW,
    NODE_IMPACT,
    NODE_INTAKE,
    NODE_POLICY,
    NODE_RELIABILITY,
    NODE_SECURITY,
    NODE_TEST_STRATEGY,
    route_after_adjudication,
    route_after_intake,
)

logger = structlog.get_logger(__name__)

# ── Singleton agent instances (stateless, safe to reuse) ──────────────────
_intake_agent       = IntakeAgent()
_impact_agent       = ImpactAgent()
_policy_agent       = PolicyAgent()
_security_agent     = SecurityAgent()
_test_strategy_agent = TestStrategyAgent()
_reliability_agent  = ReliabilityAgent()
_evidence_packager  = EvidencePackagerAgent()
_adjudication_agent = AdjudicationAgent()


# ── Node wrapper functions ─────────────────────────────────────────────────
# Each wrapper updates the CaseStatus before delegating to the agent's run().
# This ensures the status is always accurate in persisted state checkpoints.

def _make_agent_node(agent_instance, status: CaseStatus):  # type: ignore[no-untyped-def]
    """Factory: return a LangGraph node function for the given agent."""

    def node_fn(state: GraphState) -> GraphState:
        wf = state["workflow"]
        wf.status = status
        logger.debug("node_entered", node=agent_instance.agent_name, case_id=wf.case.case_id)
        updated_wf = agent_instance.run(wf)
        logger.debug("node_exited", node=agent_instance.agent_name, case_id=wf.case.case_id)
        return {"workflow": updated_wf}

    node_fn.__name__ = agent_instance.agent_name  # makes graph visualisation readable
    return node_fn


def _human_review_node(state: GraphState) -> GraphState:
    """
    Human-in-the-loop checkpoint node.

    In Step 11, this node will be wired to LangGraph's interrupt()
    mechanism to pause execution and wait for a human decision payload.
    For now it is a passthrough that logs the escalation.
    """
    wf = state["workflow"]
    logger.info(
        "human_review_checkpoint",
        case_id=wf.case.case_id,
        escalations=[e.reasons for e in wf.escalations],
    )
    # Passthrough — human decision handling added in Step 11
    return {"workflow": wf}


def build_graph() -> StateGraph:
    """
    Assemble and return the compiled Change Review Orchestrator graph.

    Returns:
        A compiled LangGraph StateGraph ready for invocation.

    Example:
        graph = build_graph()
        result = graph.invoke({"workflow": initial_state})
    """
    graph = StateGraph(GraphState)

    # ── Register nodes ─────────────────────────────────────────────────────
    graph.add_node("supervisor",          supervisor_node)
    graph.add_node(NODE_INTAKE,           _make_agent_node(_intake_agent,        CaseStatus.INTAKE))
    graph.add_node(NODE_IMPACT,           _make_agent_node(_impact_agent,         CaseStatus.IMPACT))
    graph.add_node(NODE_POLICY,           _make_agent_node(_policy_agent,         CaseStatus.POLICY))
    graph.add_node(NODE_SECURITY,         _make_agent_node(_security_agent,       CaseStatus.SECURITY))
    graph.add_node(NODE_TEST_STRATEGY,    _make_agent_node(_test_strategy_agent,  CaseStatus.TEST_STRATEGY))
    graph.add_node(NODE_RELIABILITY,      _make_agent_node(_reliability_agent,    CaseStatus.RELIABILITY))
    graph.add_node(NODE_EVIDENCE_PACKAGER, _make_agent_node(_evidence_packager,   CaseStatus.PACKAGING))
    graph.add_node(NODE_ADJUDICATION,     _make_agent_node(_adjudication_agent,   CaseStatus.ADJUDICATION))
    graph.add_node(NODE_HUMAN_REVIEW,     _human_review_node)

    # ── Edges ──────────────────────────────────────────────────────────────
    graph.add_edge(START, "supervisor")

    # Supervisor → Intake (always)
    graph.add_edge("supervisor", NODE_INTAKE)

    # Intake → Impact or END (conditional on intake success)
    graph.add_conditional_edges(
        NODE_INTAKE,
        route_after_intake,
        {NODE_IMPACT: NODE_IMPACT, NODE_END: END},
    )

    # Impact → Policy (sequential for now; fan-out via Send added in Step 11)
    graph.add_edge(NODE_IMPACT, NODE_POLICY)
    graph.add_edge(NODE_POLICY, NODE_SECURITY)
    graph.add_edge(NODE_SECURITY, NODE_TEST_STRATEGY)
    graph.add_edge(NODE_TEST_STRATEGY, NODE_RELIABILITY)
    graph.add_edge(NODE_RELIABILITY, NODE_EVIDENCE_PACKAGER)

    # Evidence Packager → Adjudication (always)
    graph.add_edge(NODE_EVIDENCE_PACKAGER, NODE_ADJUDICATION)

    # Adjudication → Human Review or END (conditional)
    graph.add_conditional_edges(
        NODE_ADJUDICATION,
        route_after_adjudication,
        {NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW, NODE_END: END},
    )

    # Human review always terminates
    graph.add_edge(NODE_HUMAN_REVIEW, END)

    logger.info("graph_assembled", nodes=list(graph.nodes))

    return graph.compile()
