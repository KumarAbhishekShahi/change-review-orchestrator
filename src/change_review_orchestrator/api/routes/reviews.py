"""
Review API Routes — Change Review Orchestrator

Endpoints:
  POST /api/v1/reviews/sync        — Run full pipeline synchronously, return result
  POST /api/v1/reviews             — (future) Queue async review, return case_id
  GET  /api/v1/reviews/{case_id}   — Get review status and result
  GET  /api/v1/reviews             — List recent reviews (in-memory store)
  GET  /api/v1/reviews/{case_id}/report  — Download Markdown report
  GET  /api/v1/reviews/{case_id}/bundle  — Download JSON audit bundle
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse

from change_review_orchestrator.api.pipeline_runner import build_case_from_request, run_pipeline
from change_review_orchestrator.api.schemas import (
    AdjudicationSummary,
    AgentResultSummary,
    DeploymentReadiness,
    FindingSummary,
    ReviewAcceptedResponse,
    ReviewListItem,
    ReviewListResponse,
    ReviewRequest,
    ReviewStatusResponse,
)
from change_review_orchestrator.domain.enums import Severity
from change_review_orchestrator.domain.models import WorkflowState

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

# In-memory result store keyed by case_id (replace with DB in Step 13)
_result_store: dict[str, dict[str, Any]] = {}


def _state_to_status_response(state: WorkflowState, status: str = "COMPLETED") -> ReviewStatusResponse:
    """Convert a completed WorkflowState to a ReviewStatusResponse."""
    case = state.case
    adj_meta = state.agent_results.get("adjudication")
    adj_data = adj_meta.metadata if adj_meta else {}
    rel_meta = state.agent_results.get("reliability")
    rel_data = rel_meta.metadata if rel_meta else {}
    pkg_meta = state.agent_results.get("evidence_packager")
    pkg_data = pkg_meta.metadata if pkg_meta else {}

    finding_counts = {
        "total":    len(state.all_findings),
        "critical": sum(1 for f in state.all_findings if f.severity == Severity.CRITICAL),
        "high":     sum(1 for f in state.all_findings if f.severity == Severity.HIGH),
        "medium":   sum(1 for f in state.all_findings if f.severity == Severity.MEDIUM),
        "low":      sum(1 for f in state.all_findings if f.severity == Severity.LOW),
    }

    top_findings = [
        FindingSummary(
            finding_id=f.finding_id,
            agent=f.agent,
            category=f.category.value,
            severity=f.severity.value,
            title=f.title,
            policy_reference=f.policy_reference,
            suppressed=f.suppressed,
        )
        for f in sorted(
            state.all_findings,
            key=lambda x: [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
                            Severity.LOW, Severity.INFO].index(x.severity)
        )[:10]
    ]

    agent_results = [
        AgentResultSummary(
            agent=name,
            status=ar.status.value,
            findings_count=len(ar.findings),
            summary=ar.summary,
            duration_seconds=ar.duration_seconds,
        )
        for name, ar in state.agent_results.items()
    ]

    adjudication = None
    if adj_data:
        adjudication = AdjudicationSummary(
            recommendation=adj_data.get("recommendation", "UNKNOWN"),
            composite_score=adj_data.get("composite_score", 0),
            triggered_escalations=adj_data.get("triggered_escalations", []),
            required_actions=adj_data.get("required_actions", []),
            advisory_actions=adj_data.get("advisory_actions", []),
        )

    deployment_readiness = None
    if rel_data:
        deployment_readiness = DeploymentReadiness(
            deployment_risk_score=rel_data.get("deployment_risk_score", 0),
            rollback_viable=rel_data.get("rollback_viable", True),
            rollback_score=rel_data.get("rollback_score", 0),
            observability_score=rel_data.get("observability_score", 0),
            blast_radius_score=rel_data.get("blast_radius_score", 0),
            blast_consumers=rel_data.get("blast_consumers", "unknown"),
            deployment_strategy=rel_data.get("deployment_strategy", "unknown"),
            strategy_rationale=rel_data.get("strategy_rationale", ""),
        )

    return ReviewStatusResponse(
        case_id=case.case_id,
        status=status,
        title=case.title,
        recommendation=adj_data.get("recommendation"),
        composite_score=adj_data.get("composite_score"),
        finding_counts=finding_counts,
        agent_results=agent_results,
        adjudication=adjudication,
        deployment_readiness=deployment_readiness,
        top_findings=top_findings,
        report_available=bool(pkg_data.get("report_path") and
                               Path(pkg_data["report_path"]).exists()),
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


@router.post("/sync", response_model=ReviewStatusResponse, status_code=200)
async def create_review_sync(request: ReviewRequest) -> ReviewStatusResponse:
    """
    Run the full pipeline synchronously and return the complete result.

    Suitable for development, testing, and low-volume production use.
    For high-throughput scenarios use POST /reviews (async queue).
    """
    log = logger.bind(title=request.title)
    log.info("sync_review_requested")

    case = build_case_from_request(request.model_dump())
    state = run_pipeline(case)

    response = _state_to_status_response(state)
    _result_store[case.case_id] = {
        "state": state,
        "response": response,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    log.info("sync_review_complete",
             case_id=case.case_id,
             recommendation=response.recommendation)
    return response


@router.post("", response_model=ReviewAcceptedResponse, status_code=202)
async def create_review_async(request: ReviewRequest) -> ReviewAcceptedResponse:
    """
    Accept a review request and run synchronously (simulated async).
    Returns a case_id for polling via GET /reviews/{case_id}.
    """
    case = build_case_from_request(request.model_dump())
    state = run_pipeline(case)
    response = _state_to_status_response(state)
    _result_store[case.case_id] = {
        "state": state,
        "response": response,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return ReviewAcceptedResponse(
        case_id=case.case_id,
        status="COMPLETED",
        message="Review pipeline completed.",
        status_url=f"/api/v1/reviews/{case.case_id}",
    )


@router.get("", response_model=ReviewListResponse)
async def list_reviews() -> ReviewListResponse:
    """List all reviews in the in-memory store (most recent first)."""
    items = [
        ReviewListItem(
            case_id=case_id,
            title=entry["response"].title,
            status=entry["response"].status,
            recommendation=entry["response"].recommendation,
            composite_score=entry["response"].composite_score,
            created_at=entry["created_at"],
        )
        for case_id, entry in reversed(list(_result_store.items()))
    ]
    return ReviewListResponse(items=items, total=len(items))


@router.get("/{case_id}", response_model=ReviewStatusResponse)
async def get_review(case_id: str) -> ReviewStatusResponse:
    """Get the full review result for a case_id."""
    entry = _result_store.get(case_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Review not found: {case_id}")
    return entry["response"]


@router.get("/{case_id}/report", response_class=PlainTextResponse)
async def get_review_report(case_id: str) -> str:
    """Download the Markdown review report for a completed review."""
    entry = _result_store.get(case_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Review not found: {case_id}")
    state: WorkflowState = entry["state"]
    pkg_meta = state.agent_results.get("evidence_packager")
    if not pkg_meta:
        raise HTTPException(status_code=404, detail="Report not yet available.")
    report_path = pkg_meta.metadata.get("report_path")
    if not report_path or not Path(report_path).exists():
        raise HTTPException(status_code=404, detail="Report file not found on disk.")
    return Path(report_path).read_text(encoding="utf-8")


@router.get("/{case_id}/bundle")
async def get_audit_bundle(case_id: str) -> Response:
    """Download the JSON audit bundle for a completed review."""
    entry = _result_store.get(case_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Review not found: {case_id}")
    state: WorkflowState = entry["state"]
    pkg_meta = state.agent_results.get("evidence_packager")
    if not pkg_meta:
        raise HTTPException(status_code=404, detail="Bundle not yet available.")
    bundle_path = pkg_meta.metadata.get("bundle_path")
    if not bundle_path or not Path(bundle_path).exists():
        raise HTTPException(status_code=404, detail="Bundle file not found on disk.")
    content = Path(bundle_path).read_text(encoding="utf-8")
    return Response(content=content, media_type="application/json")
