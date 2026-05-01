"""
Supervisor node for the Change Review Orchestrator workflow.

The supervisor sits at the START of the graph and is responsible for:
1. Stamping the pipeline start time onto WorkflowState
2. Logging the incoming case summary for audit purposes
3. Setting the initial CaseStatus to INTAKE

The supervisor is intentionally lightweight — its job is to initialise
the pipeline run, not to make decisions. Routing decisions live in
transitions.py.
"""

from __future__ import annotations

import datetime

import structlog

from change_review_orchestrator.domain.enums import CaseStatus
from change_review_orchestrator.workflow.state import GraphState

logger = structlog.get_logger(__name__)


def supervisor_node(state: GraphState) -> GraphState:
    """
    Supervisor / pipeline initialiser node.

    Called once at the start of every pipeline run. Stamps the
    start time and logs the case summary so every run is traceable
    from the very first log line.

    Args:
        state: Incoming GraphState (workflow.status == PENDING).

    Returns:
        Updated GraphState with pipeline_started_at set and
        status transitioned to INTAKE.
    """
    wf = state["workflow"]

    logger.info(
        "pipeline_started",
        case_id=wf.case.case_id,
        title=wf.case.title,
        repository=wf.case.repository,
        branch=wf.case.branch,
        change_type=wf.case.change_type.value,
        data_classification=wf.case.data_classification.value,
        files_changed=wf.case.total_files_changed,
        has_breaking_changes=wf.case.has_breaking_changes,
    )

    wf.pipeline_started_at = datetime.datetime.now(datetime.timezone.utc)
    wf.status = CaseStatus.INTAKE

    return {"workflow": wf}
