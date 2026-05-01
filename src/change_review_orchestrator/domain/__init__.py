"""
Domain layer for Change Review Orchestrator.

Re-exports the most commonly used symbols so callers can write:
    from change_review_orchestrator.domain import ChangeCase, Finding, Severity
instead of navigating to sub-modules.
"""

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
from change_review_orchestrator.domain.models import (
    AgentResult,
    ChangeCase,
    ChangedFile,
    EscalationRecord,
    EvidenceItem,
    Finding,
    WorkflowState,
)
from change_review_orchestrator.domain.serializers import (
    change_case_to_json,
    from_json,
    to_json,
    workflow_state_to_json,
)

__all__ = [
    # Enums
    "AgentStatus",
    "AssetCategory",
    "CaseStatus",
    "ChangeType",
    "DataClassification",
    "EscalationReason",
    "FindingCategory",
    "Recommendation",
    "RiskLevel",
    "Severity",
    # Models
    "AgentResult",
    "ChangeCase",
    "ChangedFile",
    "EscalationRecord",
    "EvidenceItem",
    "Finding",
    "WorkflowState",
    # Serialisers
    "change_case_to_json",
    "from_json",
    "to_json",
    "workflow_state_to_json",
]
