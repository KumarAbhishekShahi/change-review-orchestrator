"""
Core Pydantic domain models for Change Review Orchestrator.

These schemas form the canonical contract between all agents and the
persistence layer. Every model uses strict validation and exports
cleanly to JSON for audit bundles.

Design decisions:
- All IDs are UUIDs generated at creation time.
- Timestamps are always UTC-aware datetimes.
- Models are immutable by default (model_config frozen=True) except
  WorkflowState which is updated in-place by agents.
- Optional fields default to None rather than empty strings.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    CaseStatus,
    ChangeType,
    DataClassification,
    EscalationReason,
    FindingCategory,
    Recommendation,
    RiskLevel,
    Severity,
)


def _utcnow() -> datetime.datetime:
    """Return the current UTC-aware datetime. Centralised for testability."""
    return datetime.datetime.now(datetime.timezone.utc)


def _new_uuid() -> str:
    """Return a new UUID4 string. Centralised so tests can patch it."""
    return str(uuid.uuid4())


# ── Changed File ──────────────────────────────────────────────────────────────

class ChangedFile(BaseModel):
    """
    Represents a single file modified in the change request.

    Populated by the Intake agent and enriched by the Impact agent.
    """

    model_config = {"frozen": True}

    path: str = Field(..., description="Relative file path from repository root")
    category: AssetCategory = Field(
        default=AssetCategory.UNKNOWN,
        description="Asset category inferred from path and extension",
    )
    lines_added: int = Field(default=0, ge=0)
    lines_removed: int = Field(default=0, ge=0)
    is_breaking_change: bool = Field(
        default=False,
        description="True when heuristics detect a backwards-incompatible change",
    )
    breaking_change_reason: str | None = Field(
        default=None,
        description="Human-readable explanation of why this is flagged as breaking",
    )

    @property
    def churn(self) -> int:
        """Total lines changed (added + removed). Used in risk scoring."""
        return self.lines_added + self.lines_removed


# ── Evidence Item ─────────────────────────────────────────────────────────────

class EvidenceItem(BaseModel):
    """
    A single piece of evidence collected during the review.

    Evidence items form the audit trail. They are immutable once created.
    """

    model_config = {"frozen": True}

    evidence_id: str = Field(default_factory=_new_uuid)
    source_agent: str = Field(..., description="Name of the agent that created this item")
    label: str = Field(..., description="Short label, e.g. 'PR Description', 'SAST Report'")
    content_summary: str = Field(..., description="Human-readable summary of the evidence")
    artifact_path: str | None = Field(
        default=None,
        description="Relative path to the full artifact file on disk",
    )
    collected_at: datetime.datetime = Field(default_factory=_utcnow)


# ── Finding ───────────────────────────────────────────────────────────────────

class Finding(BaseModel):
    """
    A structured finding raised by any agent during review.

    Findings drive the adjudication recommendation. CRITICAL or HIGH
    severity findings automatically trigger escalation unless suppressed.
    """

    model_config = {"frozen": True}

    finding_id: str = Field(default_factory=_new_uuid)
    agent: str = Field(..., description="Name of the agent that raised the finding")
    category: FindingCategory
    severity: Severity
    title: str = Field(..., max_length=200)
    description: str = Field(..., description="Detailed explanation of the finding")
    affected_assets: list[str] = Field(
        default_factory=list,
        description="File paths or component names affected",
    )
    policy_reference: str | None = Field(
        default=None,
        description="Policy ID or section reference, if applicable",
    )
    remediation_guidance: str | None = Field(
        default=None,
        description="Recommended action to resolve this finding",
    )
    raised_at: datetime.datetime = Field(default_factory=_utcnow)
    suppressed: bool = Field(
        default=False,
        description="Set True when a human reviewer acknowledges and waives this finding",
    )
    suppression_note: str | None = None


# ── Agent Result ──────────────────────────────────────────────────────────────

class AgentResult(BaseModel):
    """
    Output produced by a single agent execution.

    Each agent writes exactly one AgentResult to the workflow state.
    Results are merged by the Evidence Packager.
    """

    agent_name: str
    status: AgentStatus = AgentStatus.NOT_STARTED
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    findings: list[Finding] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    summary: str = Field(default="", description="One-paragraph narrative summary")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent-specific structured data not captured in findings",
    )
    error_message: str | None = Field(
        default=None,
        description="Set if the agent encountered a non-fatal error",
    )

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock seconds the agent took to run."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def max_severity(self) -> Severity | None:
        """Highest severity finding raised by this agent, or None."""
        if not self.findings:
            return None
        active = [f for f in self.findings if not f.suppressed]
        if not active:
            return None
        return max(active, key=lambda f: f.severity.numeric()).severity


# ── Escalation Record ─────────────────────────────────────────────────────────

class EscalationRecord(BaseModel):
    """
    Records that a case was escalated to human review and why.
    """

    model_config = {"frozen": True}

    escalation_id: str = Field(default_factory=_new_uuid)
    reasons: list[EscalationReason]
    triggered_by_agent: str
    triggered_at: datetime.datetime = Field(default_factory=_utcnow)
    assigned_to: str | None = Field(
        default=None,
        description="User/group assigned for human review",
    )
    resolved_at: datetime.datetime | None = None
    resolution_note: str | None = None


# ── Change Case (canonical input schema) ─────────────────────────────────────

class ChangeCase(BaseModel):
    """
    Canonical representation of a change request entering the pipeline.

    The Intake agent normalises any raw input (PR webhook, JIRA payload,
    manual submission) into this schema before the workflow starts.
    """

    case_id: str = Field(default_factory=_new_uuid)
    created_at: datetime.datetime = Field(default_factory=_utcnow)
    updated_at: datetime.datetime = Field(default_factory=_utcnow)

    # Source metadata
    source_system: str = Field(
        default="manual",
        description="Origin system: 'github', 'gitlab', 'jira', 'manual'",
    )
    source_ref: str | None = Field(
        default=None,
        description="PR number, JIRA ticket ID, or other external reference",
    )
    repository: str | None = Field(default=None, description="e.g. org/repo-name")
    branch: str | None = None
    target_branch: str | None = Field(default="main")
    commit_sha: str | None = None

    # Change description
    title: str = Field(..., min_length=3, max_length=500)
    description: str = Field(default="")
    change_type: ChangeType = Field(default=ChangeType.UNKNOWN)
    data_classification: DataClassification = Field(
        default=DataClassification.INTERNAL,
        description="Sensitivity of data handled by the changed system",
    )

    # Changed assets
    changed_files: list[ChangedFile] = Field(default_factory=list)

    # Review metadata
    author: str | None = None
    reviewers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    jira_ticket: str | None = None
    release_version: str | None = None
    target_environment: str | None = Field(
        default="production",
        description="Intended deployment target",
    )

    # Derived / set by intake agent
    total_files_changed: int = Field(default=0, ge=0)
    total_lines_added: int = Field(default=0, ge=0)
    total_lines_removed: int = Field(default=0, ge=0)
    has_breaking_changes: bool = False
    missing_metadata_fields: list[str] = Field(
        default_factory=list,
        description="Required fields that were absent in the original payload",
    )

    @model_validator(mode="after")
    def compute_totals(self) -> "ChangeCase":
        """Auto-compute aggregate counts from the changed files list."""
        if self.changed_files:
            self.total_files_changed = len(self.changed_files)
            self.total_lines_added = sum(f.lines_added for f in self.changed_files)
            self.total_lines_removed = sum(f.lines_removed for f in self.changed_files)
            self.has_breaking_changes = any(f.is_breaking_change for f in self.changed_files)
        return self

    @field_validator("commit_sha")
    @classmethod
    def validate_sha(cls, v: str | None) -> str | None:
        """Accept full (40-char) or short (7-char) git SHAs only."""
        if v is not None and len(v) not in (7, 40):
            raise ValueError(f"commit_sha must be 7 or 40 characters, got {len(v)}")
        return v


# ── Workflow State (mutable, managed by LangGraph) ────────────────────────────

class WorkflowState(BaseModel):
    """
    Shared mutable state passed between all LangGraph nodes.

    LangGraph serialises this object between node executions, so all
    fields must be JSON-serialisable. The supervisor reads this state
    to decide which node runs next.

    Note: Unlike other models, WorkflowState is NOT frozen — agents
    update it in place.
    """

    # Core case reference
    case: ChangeCase

    # Current pipeline position
    status: CaseStatus = CaseStatus.PENDING

    # Per-agent results — keyed by agent name
    agent_results: dict[str, AgentResult] = Field(default_factory=dict)

    # Accumulated evidence and findings (flattened from agent results)
    all_findings: list[Finding] = Field(default_factory=list)
    all_evidence: list[EvidenceItem] = Field(default_factory=list)

    # Escalation records
    escalations: list[EscalationRecord] = Field(default_factory=list)

    # Final output (set by adjudication agent)
    final_recommendation: Recommendation | None = None
    final_summary: str = ""
    report_path: str | None = Field(
        default=None,
        description="Path to the generated Markdown report artefact",
    )
    audit_bundle_path: str | None = Field(
        default=None,
        description="Path to the generated JSON audit bundle artefact",
    )

    # Pipeline control
    pipeline_started_at: datetime.datetime | None = None
    pipeline_completed_at: datetime.datetime | None = None
    error_log: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        """True when the pipeline has reached a final state."""
        return self.status in (
            CaseStatus.COMPLETED,
            CaseStatus.ESCALATED,
            CaseStatus.BLOCKED,
            CaseStatus.FAILED,
        )

    @property
    def max_severity_across_all_agents(self) -> Severity | None:
        """Highest severity finding across every agent result."""
        active = [f for f in self.all_findings if not f.suppressed]
        if not active:
            return None
        return max(active, key=lambda f: f.severity.numeric()).severity
