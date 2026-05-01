"""
Review Repository — Change Review Orchestrator

Repository pattern wrapping all database operations.
All methods accept an SQLAlchemy Session so callers control transaction scope.

Operations:
  save_review(state)         — Upsert a ReviewRecord from a WorkflowState
  save_findings(state)       — Bulk-insert all FindingRecords
  save_agent_results(state)  — Bulk-upsert all AgentResultRecords
  get_review(case_id)        — Fetch ReviewRecord by case_id
  list_reviews(limit, offset)— Paginated list of ReviewRecords
  get_findings(case_id)      — All FindingRecords for a case
  delete_review(case_id)     — Hard delete (cascade to findings + agent_results)
"""

from __future__ import annotations

import datetime
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from change_review_orchestrator.domain.enums import Severity
from change_review_orchestrator.domain.models import WorkflowState
from change_review_orchestrator.persistence.models import (
    AgentResultRecord,
    FindingRecord,
    ReviewRecord,
)

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ── Case payload serialiser ───────────────────────────────────────────────────

def _serialise_case(state: WorkflowState) -> dict:
    """Convert ChangeCase to a JSON-serialisable dict."""
    case = state.case
    return {
        "case_id":           case.case_id,
        "title":             case.title,
        "source_system":     case.source_system,
        "source_ref":        case.source_ref,
        "repository":        case.repository,
        "branch":            case.branch,
        "author":            case.author,
        "commit_sha":        case.commit_sha,
        "change_type":       case.change_type.value,
        "data_classification": case.data_classification.value,
        "jira_ticket":       case.jira_ticket,
        "release_version":   case.release_version,
        "has_breaking_changes": case.has_breaking_changes,
        "reviewers":         case.reviewers,
        "labels":            case.labels,
        "description":       case.description,
        "changed_files": [
            {
                "path":              cf.path,
                "category":          cf.category.value,
                "lines_added":       cf.lines_added,
                "lines_removed":     cf.lines_removed,
                "is_binary":         cf.is_binary,
                "is_breaking_change": cf.is_breaking_change,
            }
            for cf in case.changed_files
        ],
    }


# ── Write operations ──────────────────────────────────────────────────────────

def save_review(db: Session, state: WorkflowState, status: str = "COMPLETED") -> ReviewRecord:
    """
    Upsert a ReviewRecord from a completed WorkflowState.
    If the record already exists it is updated in place.
    """
    case = state.case
    adj_meta  = state.agent_results.get("adjudication")
    adj_data  = adj_meta.metadata if adj_meta else {}
    rel_meta  = state.agent_results.get("reliability")
    rel_data  = rel_meta.metadata if rel_meta else {}
    pkg_meta  = state.agent_results.get("evidence_packager")
    pkg_data  = pkg_meta.metadata if pkg_meta else {}

    findings  = state.all_findings
    agent_metadata = {
        name: ar.metadata
        for name, ar in state.agent_results.items()
    }

    existing: Optional[ReviewRecord] = db.get(ReviewRecord, case.case_id)

    if existing is None:
        record = ReviewRecord(case_id=case.case_id)
        db.add(record)
    else:
        record = existing

    record.status            = status
    record.title             = case.title
    record.author            = case.author
    record.repository        = case.repository
    record.branch            = case.branch
    record.source_system     = case.source_system
    record.source_ref        = case.source_ref
    record.commit_sha        = case.commit_sha
    record.change_type       = case.change_type.value
    record.data_classification = case.data_classification.value
    record.jira_ticket       = case.jira_ticket
    record.release_version   = case.release_version
    record.has_breaking_changes = case.has_breaking_changes
    record.recommendation    = adj_data.get("recommendation")
    record.composite_score   = adj_data.get("composite_score")
    record.required_actions  = adj_data.get("required_actions", [])
    record.advisory_actions  = adj_data.get("advisory_actions", [])
    record.findings_total    = len(findings)
    record.findings_critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    record.findings_high     = sum(1 for f in findings if f.severity == Severity.HIGH)
    record.findings_medium   = sum(1 for f in findings if f.severity == Severity.MEDIUM)
    record.findings_low      = sum(1 for f in findings if f.severity == Severity.LOW)
    record.deployment_strategy = rel_data.get("deployment_strategy")
    record.blast_consumers   = rel_data.get("blast_consumers")
    record.rollback_viable   = rel_data.get("rollback_viable")
    record.report_path       = pkg_data.get("report_path")
    record.bundle_path       = pkg_data.get("bundle_path")
    record.case_payload      = _serialise_case(state)
    record.agent_metadata    = agent_metadata
    record.completed_at      = _utcnow()

    db.flush()
    logger.info("review_saved", case_id=case.case_id, status=status)
    return record


def save_findings(db: Session, state: WorkflowState) -> int:
    """
    Bulk-insert all FindingRecords for a case.
    Existing findings for the case are deleted first (replace strategy).
    Returns the count of inserted records.
    """
    case_id = state.case.case_id

    # Delete existing findings for idempotency
    existing = db.query(FindingRecord).filter(FindingRecord.case_id == case_id).all()
    for row in existing:
        db.delete(row)
    db.flush()

    for f in state.all_findings:
        record = FindingRecord(
            finding_id=f.finding_id,
            case_id=case_id,
            agent=f.agent,
            category=f.category.value,
            severity=f.severity.value,
            title=f.title,
            description=f.description,
            remediation_guidance=f.remediation_guidance,
            policy_reference=f.policy_reference,
            affected_assets=f.affected_assets,
            suppressed=f.suppressed,
        )
        db.add(record)

    db.flush()
    logger.info("findings_saved", case_id=case_id, count=len(state.all_findings))
    return len(state.all_findings)


def save_agent_results(db: Session, state: WorkflowState) -> int:
    """
    Upsert AgentResultRecords for all agents in a WorkflowState.
    Returns count of upserted records.
    """
    case_id = state.case.case_id
    count = 0

    for agent_name, ar in state.agent_results.items():
        existing = db.get(AgentResultRecord, (case_id, agent_name))
        if existing is None:
            record = AgentResultRecord(case_id=case_id, agent=agent_name)
            db.add(record)
        else:
            record = existing

        record.status           = ar.status.value
        record.summary          = ar.summary
        record.duration_seconds = ar.duration_seconds
        record.findings_count   = len(ar.findings)
        record.metadata_blob    = ar.metadata
        record.error_message    = ar.error_message
        count += 1

    db.flush()
    logger.info("agent_results_saved", case_id=case_id, count=count)
    return count


# ── Read operations ───────────────────────────────────────────────────────────

def get_review(db: Session, case_id: str) -> Optional[ReviewRecord]:
    """Fetch a ReviewRecord by case_id. Returns None if not found."""
    return db.get(ReviewRecord, case_id)


def list_reviews(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
) -> list[ReviewRecord]:
    """
    Return a paginated list of ReviewRecords ordered by created_at DESC.

    Args:
        limit:         Maximum rows to return (default 50, max 200).
        offset:        Row offset for pagination.
        status_filter: Optional status value to filter on.
    """
    limit = min(limit, 200)
    query = db.query(ReviewRecord).order_by(ReviewRecord.created_at.desc())
    if status_filter:
        query = query.filter(ReviewRecord.status == status_filter)
    return query.limit(limit).offset(offset).all()


def count_reviews(db: Session, status_filter: Optional[str] = None) -> int:
    """Return total review count (with optional status filter)."""
    query = db.query(ReviewRecord)
    if status_filter:
        query = query.filter(ReviewRecord.status == status_filter)
    return query.count()


def get_findings(db: Session, case_id: str) -> list[FindingRecord]:
    """Return all FindingRecords for a case_id, ordered by severity."""
    _SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    records = (
        db.query(FindingRecord)
        .filter(FindingRecord.case_id == case_id)
        .all()
    )
    return sorted(records, key=lambda r: _SEV_ORDER.index(r.severity)
                  if r.severity in _SEV_ORDER else 99)


def get_agent_results(db: Session, case_id: str) -> list[AgentResultRecord]:
    """Return all AgentResultRecords for a case_id."""
    return (
        db.query(AgentResultRecord)
        .filter(AgentResultRecord.case_id == case_id)
        .all()
    )


def delete_review(db: Session, case_id: str) -> bool:
    """
    Hard-delete a review and all its findings + agent results (via CASCADE).
    Returns True if deleted, False if not found.
    """
    record = db.get(ReviewRecord, case_id)
    if record is None:
        return False
    db.delete(record)
    db.flush()
    logger.info("review_deleted", case_id=case_id)
    return True
