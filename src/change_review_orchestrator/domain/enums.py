"""
Domain enumerations for Change Review Orchestrator.

All status codes, severity levels, recommendation outcomes, and
classification labels used across agents and schemas are defined here.
Using enums prevents magic strings and makes exhaustiveness checking
possible with mypy.
"""

from __future__ import annotations

from enum import Enum


# ── Workflow / Case lifecycle ─────────────────────────────────────────────────

class CaseStatus(str, Enum):
    """Lifecycle state of a change review case."""

    PENDING = "PENDING"               # Case received, not yet started
    INTAKE = "INTAKE"                 # Intake agent running
    IMPACT = "IMPACT"                 # Impact agent running
    POLICY = "POLICY"                 # Policy agent running
    SECURITY = "SECURITY"             # Security agent running
    TEST_STRATEGY = "TEST_STRATEGY"   # Test strategy agent running
    RELIABILITY = "RELIABILITY"       # Reliability agent running
    PACKAGING = "PACKAGING"           # Evidence packager running
    ADJUDICATION = "ADJUDICATION"     # Adjudication agent running
    COMPLETED = "COMPLETED"           # Pipeline finished
    ESCALATED = "ESCALATED"           # Sent to human review queue
    BLOCKED = "BLOCKED"               # Hard block — must not release
    FAILED = "FAILED"                 # Unrecoverable pipeline error


class AgentStatus(str, Enum):
    """Execution status of a single agent run."""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"       # Agent not applicable for this change type
    FAILED = "FAILED"


# ── Risk / Severity ───────────────────────────────────────────────────────────

class Severity(str, Enum):
    """
    Severity of a finding raised by any agent.

    Ordered from lowest to highest risk. The adjudication agent uses the
    maximum severity across all findings to drive the recommendation.
    """

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    def numeric(self) -> int:
        """Return a numeric weight for comparison logic."""
        return {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}[self.value]

    def __gt__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.numeric() > other.numeric()

    def __ge__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.numeric() >= other.numeric()


class RiskLevel(str, Enum):
    """Deployment risk level assessed by the Reliability agent."""

    MINIMAL = "MINIMAL"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ── Recommendations ───────────────────────────────────────────────────────────

class Recommendation(str, Enum):
    """
    Final adjudicated recommendation for the change request.

    APPROVE  — all checks passed; safe to merge/deploy
    APPROVE_WITH_CONDITIONS — passes with mandatory follow-up items
    ESCALATE — requires human sign-off before proceeding
    BLOCK    — hard stop; must not be merged/deployed until remediated
    """

    APPROVE = "APPROVE"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


# ── Change / Asset classification ─────────────────────────────────────────────

class ChangeType(str, Enum):
    """High-level category of a change request."""

    FEATURE = "FEATURE"
    BUG_FIX = "BUG_FIX"
    HOTFIX = "HOTFIX"
    REFACTOR = "REFACTOR"
    DEPENDENCY_UPGRADE = "DEPENDENCY_UPGRADE"
    CONFIGURATION = "CONFIGURATION"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    DATABASE_MIGRATION = "DATABASE_MIGRATION"
    SECURITY_PATCH = "SECURITY_PATCH"
    DOCUMENTATION = "DOCUMENTATION"
    UNKNOWN = "UNKNOWN"


class AssetCategory(str, Enum):
    """Category of a file/asset changed in the PR."""

    SOURCE_CODE = "SOURCE_CODE"
    TEST = "TEST"
    CONFIGURATION = "CONFIGURATION"
    INFRASTRUCTURE_AS_CODE = "INFRASTRUCTURE_AS_CODE"
    DATABASE_MIGRATION = "DATABASE_MIGRATION"
    API_SCHEMA = "API_SCHEMA"
    DEPENDENCY_MANIFEST = "DEPENDENCY_MANIFEST"
    DOCUMENTATION = "DOCUMENTATION"
    CI_CD_PIPELINE = "CI_CD_PIPELINE"
    SECRET_OR_CREDENTIAL = "SECRET_OR_CREDENTIAL"   # should never appear; flagged
    UNKNOWN = "UNKNOWN"


class DataClassification(str, Enum):
    """
    Data sensitivity classification for the system being changed.

    Maps to policy obligation tiers in the Policy & Control agent.
    """

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"       # PII, PCI, HIPAA, banking secrets
    HIGHLY_RESTRICTED = "HIGHLY_RESTRICTED"  # AML, fraud models, key material


# ── Finding categories ────────────────────────────────────────────────────────

class FindingCategory(str, Enum):
    """Broad category that a finding belongs to."""

    SECURITY = "SECURITY"
    POLICY = "POLICY"
    TEST_GAP = "TEST_GAP"
    IMPACT = "IMPACT"
    RELIABILITY = "RELIABILITY"
    COMPLIANCE = "COMPLIANCE"
    DOCUMENTATION = "DOCUMENTATION"
    OPERATIONAL = "OPERATIONAL"


class EscalationReason(str, Enum):
    """Reason a case was escalated to human review."""

    HIGH_SEVERITY_FINDING = "HIGH_SEVERITY_FINDING"
    CRITICAL_ASSET_MODIFIED = "CRITICAL_ASSET_MODIFIED"
    POLICY_EXCEPTION_REQUIRED = "POLICY_EXCEPTION_REQUIRED"
    MISSING_APPROVER = "MISSING_APPROVER"
    REGULATED_DATA_IMPACTED = "REGULATED_DATA_IMPACTED"
    MANUAL_OVERRIDE_REQUESTED = "MANUAL_OVERRIDE_REQUESTED"
    BREAKING_CHANGE_DETECTED = "BREAKING_CHANGE_DETECTED"
