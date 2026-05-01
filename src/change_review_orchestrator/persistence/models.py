"""
SQLAlchemy ORM Models — Change Review Orchestrator

Tables:
  reviews        — one row per ChangeCase (case_id PK)
  review_findings — one row per Finding (finding_id PK, FK → reviews)
  agent_results  — one row per agent per review (composite PK)

Design:
  - JSON columns store variable-depth metadata blobs
  - All timestamps are UTC stored as TIMESTAMP WITH TIME ZONE
  - No ORM relationships defined to keep models simple and portable
    (joins done at repository layer)
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ReviewRecord(Base):
    """
    Persisted representation of a ChangeCase + its pipeline status.
    One row per submitted review.
    """
    __tablename__ = "reviews"

    case_id          = Column(String(64),  primary_key=True)
    status           = Column(String(32),  nullable=False, default="QUEUED")
    title            = Column(String(500), nullable=False)
    author           = Column(String(255), nullable=True)
    repository       = Column(String(255), nullable=True)
    branch           = Column(String(255), nullable=True)
    source_system    = Column(String(64),  nullable=True)
    source_ref       = Column(Text,        nullable=True)
    commit_sha       = Column(String(64),  nullable=True)
    change_type      = Column(String(64),  nullable=False, default="FEATURE")
    data_classification = Column(String(64), nullable=False, default="INTERNAL")
    jira_ticket      = Column(String(64),  nullable=True)
    release_version  = Column(String(64),  nullable=True)
    has_breaking_changes = Column(Boolean, nullable=False, default=False)

    # Adjudication outcome
    recommendation   = Column(String(32),  nullable=True)
    composite_score  = Column(Integer,     nullable=True)

    # Aggregate finding counts
    findings_total   = Column(Integer,     nullable=False, default=0)
    findings_critical = Column(Integer,    nullable=False, default=0)
    findings_high    = Column(Integer,     nullable=False, default=0)
    findings_medium  = Column(Integer,     nullable=False, default=0)
    findings_low     = Column(Integer,     nullable=False, default=0)

    # Reliability snapshot
    deployment_strategy = Column(String(64), nullable=True)
    blast_consumers  = Column(String(32),  nullable=True)
    rollback_viable  = Column(Boolean,     nullable=True)

    # Full case payload and metadata stored as JSON blobs
    case_payload     = Column(JSON,  nullable=True)   # serialised ChangeCase
    agent_metadata   = Column(JSON,  nullable=True)   # per-agent metadata dict
    required_actions = Column(JSON,  nullable=True)   # list[str]
    advisory_actions = Column(JSON,  nullable=True)   # list[str]

    # Artefact paths
    report_path      = Column(Text,  nullable=True)
    bundle_path      = Column(Text,  nullable=True)

    # Timestamps
    created_at       = Column(DateTime(timezone=True), nullable=False,
                               default=_utcnow)
    completed_at     = Column(DateTime(timezone=True), nullable=True)
    error_message    = Column(Text,  nullable=True)

    def __repr__(self) -> str:
        return f"<ReviewRecord case_id={self.case_id} recommendation={self.recommendation}>"


class FindingRecord(Base):
    """
    Persisted representation of a single Finding.
    One row per finding per review.
    """
    __tablename__ = "review_findings"

    finding_id       = Column(String(64),  primary_key=True)
    case_id          = Column(String(64),  ForeignKey("reviews.case_id", ondelete="CASCADE"),
                               nullable=False, index=True)
    agent            = Column(String(64),  nullable=False)
    category         = Column(String(64),  nullable=False)
    severity         = Column(String(32),  nullable=False)
    title            = Column(String(500), nullable=False)
    description      = Column(Text,        nullable=True)
    remediation_guidance = Column(Text,    nullable=True)
    policy_reference = Column(String(64),  nullable=True)
    affected_assets  = Column(JSON,        nullable=True)   # list[str]
    suppressed       = Column(Boolean,     nullable=False, default=False)
    created_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<FindingRecord {self.finding_id} [{self.severity}] {self.title[:40]}>"


class AgentResultRecord(Base):
    """
    Persisted agent execution result. Composite PK: (case_id, agent).
    """
    __tablename__ = "agent_results"

    case_id          = Column(String(64), ForeignKey("reviews.case_id", ondelete="CASCADE"),
                               nullable=False, primary_key=True)
    agent            = Column(String(64), nullable=False, primary_key=True)
    status           = Column(String(32), nullable=False)
    summary          = Column(Text,       nullable=True)
    duration_seconds = Column(Float,      nullable=True)
    findings_count   = Column(Integer,    nullable=False, default=0)
    metadata_blob    = Column(JSON,       nullable=True)
    error_message    = Column(Text,       nullable=True)
    created_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<AgentResultRecord {self.agent}@{self.case_id} {self.status}>"
