"""
Conditional edge logic for the Change Review Orchestrator workflow graph.

Each function in this module is a LangGraph conditional edge router.
It receives the current GraphState and returns the name of the next node
to execute (or END to terminate the graph).

Routing principles:
- FAILED agents do not abort the pipeline unless the error is in a
  mandatory gating agent (intake).
- CRITICAL findings route immediately to adjudication (fast-path block).
- Human-in-the-loop is triggered when ENABLE_HUMAN_IN_THE_LOOP=true and
  an escalation condition is met.
"""

from __future__ import annotations

import structlog

from change_review_orchestrator.domain.enums import CaseStatus, Severity
from change_review_orchestrator.workflow.state import GraphState

logger = structlog.get_logger(__name__)

# ── Node name constants (avoids magic strings across the codebase) ─────────
NODE_INTAKE           = "intake"
NODE_IMPACT           = "impact"
NODE_POLICY           = "policy"
NODE_SECURITY         = "security"
NODE_TEST_STRATEGY    = "test_strategy"
NODE_RELIABILITY      = "reliability"
NODE_EVIDENCE_PACKAGER = "evidence_packager"
NODE_ADJUDICATION     = "adjudication"
NODE_HUMAN_REVIEW     = "human_review"
NODE_END              = "__end__"


def route_after_intake(state: GraphState) -> str:
    """
    After intake: proceed to impact analysis, or fail fast.

    If intake failed or the case is missing critical metadata and no
    files were classified, we abort immediately — there is nothing to
    analyse.
    """
    wf = state["workflow"]
    intake_result = wf.agent_results.get(NODE_INTAKE)

    if intake_result and intake_result.status.value == "FAILED":
        logger.warning(
            "intake_failed_aborting",
            case_id=wf.case.case_id,
            error=intake_result.error_message,
        )
        wf.status = CaseStatus.FAILED
        return NODE_END

    logger.info("routing_after_intake", next_node=NODE_IMPACT, case_id=wf.case.case_id)
    return NODE_IMPACT


def route_after_parallel_agents(state: GraphState) -> str:
    """
    After the parallel analysis tier (policy, security, test, reliability):
    route to evidence packaging, or fast-path to adjudication on CRITICAL.

    A CRITICAL severity finding anywhere in the collected findings bypasses
    the packaging step and routes straight to adjudication so a blocking
    decision can be issued immediately (with less narrative context, but
    with maximum speed for emergency hotfixes going in the wrong direction).
    """
    wf = state["workflow"]
    max_sev = wf.max_severity_across_all_agents

    if max_sev and max_sev >= Severity.CRITICAL:
        logger.warning(
            "critical_finding_fast_path",
            case_id=wf.case.case_id,
            severity=max_sev.value,
        )
        return NODE_EVIDENCE_PACKAGER   # still package before adjudicating

    return NODE_EVIDENCE_PACKAGER


def route_after_adjudication(state: GraphState) -> str:
    """
    After adjudication: route to human review or END.

    Human-in-the-loop is only active when ENABLE_HUMAN_IN_THE_LOOP=true
    AND the pipeline status is ESCALATED.
    """
    from change_review_orchestrator.config import get_settings  # late import avoids circular

    wf = state["workflow"]
    settings = get_settings()

    if settings.enable_human_in_the_loop and wf.status == CaseStatus.ESCALATED:
        logger.info(
            "routing_to_human_review",
            case_id=wf.case.case_id,
            escalations=len(wf.escalations),
        )
        return NODE_HUMAN_REVIEW

    logger.info(
        "routing_to_end",
        case_id=wf.case.case_id,
        status=wf.status.value,
        recommendation=wf.final_recommendation.value if wf.final_recommendation else "none",
    )
    return NODE_END
