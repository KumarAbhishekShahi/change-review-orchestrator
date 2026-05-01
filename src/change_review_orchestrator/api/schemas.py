"""
API Schemas — Request and Response Pydantic models for the FastAPI layer.

Separates external API contract from internal domain models.
All timestamps are ISO-8601 UTC strings in responses.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Inbound webhook payload ────────────────────────────────────────────────

class ChangedFilePayload(BaseModel):
    path: str
    lines_added: int = 0
    lines_removed: int = 0
    is_binary: bool = False
    is_breaking_change: bool = False


class ReviewRequest(BaseModel):
    """
    Inbound PR/change review request.
    Posted to POST /api/v1/reviews.
    """
    title: str = Field(..., min_length=1, max_length=500)
    source_system: Optional[str] = None          # "github" | "gitlab" | "bitbucket"
    source_ref: Optional[str] = None             # PR URL or reference
    repository: Optional[str] = None
    branch: Optional[str] = None
    author: Optional[str] = None
    commit_sha: Optional[str] = None
    change_type: Optional[str] = None            # ChangeType enum value
    data_classification: Optional[str] = None    # DataClassification enum value
    jira_ticket: Optional[str] = None
    release_version: Optional[str] = None
    reviewers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    has_breaking_changes: bool = False
    changed_files: list[ChangedFilePayload] = Field(default_factory=list)


# ── Response schemas ───────────────────────────────────────────────────────

class ReviewAcceptedResponse(BaseModel):
    """Returned immediately on POST /api/v1/reviews (async submission)."""
    case_id: str
    status: str = "QUEUED"
    message: str
    status_url: str


class FindingSummary(BaseModel):
    finding_id: str
    agent: str
    category: str
    severity: str
    title: str
    policy_reference: Optional[str] = None
    suppressed: bool = False


class AgentResultSummary(BaseModel):
    agent: str
    status: str
    findings_count: int
    summary: Optional[str] = None
    duration_seconds: Optional[float] = None


class AdjudicationSummary(BaseModel):
    recommendation: str
    composite_score: int
    triggered_escalations: list[str]
    required_actions: list[str]
    advisory_actions: list[str]


class DeploymentReadiness(BaseModel):
    deployment_risk_score: int
    rollback_viable: bool
    rollback_score: int
    observability_score: int
    blast_radius_score: int
    blast_consumers: str
    deployment_strategy: str
    strategy_rationale: str


class ReviewStatusResponse(BaseModel):
    """Returned by GET /api/v1/reviews/{case_id}."""
    case_id: str
    status: str                        # QUEUED | RUNNING | COMPLETED | FAILED
    title: str
    recommendation: Optional[str] = None
    composite_score: Optional[int] = None
    finding_counts: dict[str, int] = Field(default_factory=dict)
    agent_results: list[AgentResultSummary] = Field(default_factory=list)
    adjudication: Optional[AdjudicationSummary] = None
    deployment_readiness: Optional[DeploymentReadiness] = None
    top_findings: list[FindingSummary] = Field(default_factory=list)
    report_available: bool = False
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class ReviewListItem(BaseModel):
    case_id: str
    title: str
    status: str
    recommendation: Optional[str] = None
    composite_score: Optional[int] = None
    created_at: Optional[str] = None


class ReviewListResponse(BaseModel):
    items: list[ReviewListItem]
    total: int


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    agents_available: list[str] = Field(default_factory=list)
    llm_available: bool = False
