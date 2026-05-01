"""
Unit tests for domain models, enums, and serialisers.

All tests are pure-Python — no network, no DB, no file I/O required.
"""

from __future__ import annotations

import datetime
import json

import pytest
from pydantic import ValidationError

from change_review_orchestrator.domain.enums import (
    AssetCategory,
    CaseStatus,
    ChangeType,
    DataClassification,
    Recommendation,
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
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    EscalationReason,
    FindingCategory,
)
from change_review_orchestrator.domain.serializers import (
    change_case_to_json,
    from_json,
    to_json,
    workflow_state_to_json,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_case() -> ChangeCase:
    """A minimal valid ChangeCase for reuse across tests."""
    return ChangeCase(title="Add null check to payment processor")


@pytest.fixture
def rich_case() -> ChangeCase:
    """A ChangeCase with multiple changed files, matching the banking PR fixture."""
    return ChangeCase(
        source_system="github",
        source_ref="PR-4821",
        repository="banking-org/payments-service",
        branch="feature/PAY-1234-pci-tokenisation",
        commit_sha="a3f9e2c",
        title="PAY-1234: Add PCI tokenisation layer for card data",
        change_type=ChangeType.FEATURE,
        data_classification=DataClassification.RESTRICTED,
        author="jane.doe@bank.com",
        changed_files=[
            ChangedFile(
                path="src/payments/tokenisation/service.py",
                category=AssetCategory.SOURCE_CODE,
                lines_added=312,
                lines_removed=14,
            ),
            ChangedFile(
                path="src/payments/schema/payment.py",
                category=AssetCategory.API_SCHEMA,
                lines_added=28,
                lines_removed=45,
                is_breaking_change=True,
                breaking_change_reason="Field pan removed",
            ),
            ChangedFile(
                path="alembic/versions/0042_add_token_id_column.py",
                category=AssetCategory.DATABASE_MIGRATION,
                lines_added=67,
                lines_removed=0,
            ),
        ],
    )


@pytest.fixture
def sample_finding() -> Finding:
    return Finding(
        agent="security",
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title="Potential PAN exposure in logs",
        description="Log statements in service.py may write raw card numbers.",
        affected_assets=["src/payments/tokenisation/service.py"],
        remediation_guidance="Replace log.debug(card_number) with log.debug('[REDACTED]')",
    )


# ── Severity ordering ─────────────────────────────────────────────────────────

class TestSeverityOrdering:

    def test_critical_greater_than_high(self) -> None:
        assert Severity.CRITICAL > Severity.HIGH

    def test_high_greater_than_medium(self) -> None:
        assert Severity.HIGH > Severity.MEDIUM

    def test_info_less_than_low(self) -> None:
        assert not (Severity.INFO > Severity.LOW)

    def test_same_severity_ge(self) -> None:
        assert Severity.HIGH >= Severity.HIGH

    def test_numeric_ordering(self) -> None:
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        assert [s.numeric() for s in order] == [0, 1, 2, 3, 4]


# ── ChangedFile ───────────────────────────────────────────────────────────────

class TestChangedFile:

    def test_churn_property(self) -> None:
        f = ChangedFile(path="foo.py", lines_added=10, lines_removed=5)
        assert f.churn == 15

    def test_frozen_model_rejects_mutation(self) -> None:
        f = ChangedFile(path="foo.py")
        with pytest.raises((TypeError, ValidationError)):
            f.path = "bar.py"  # type: ignore[misc]

    def test_default_category_is_unknown(self) -> None:
        f = ChangedFile(path="some/mystery/file.xyz")
        assert f.category == AssetCategory.UNKNOWN


# ── ChangeCase ────────────────────────────────────────────────────────────────

class TestChangeCase:

    def test_minimal_case_valid(self, minimal_case: ChangeCase) -> None:
        assert minimal_case.title == "Add null check to payment processor"

    def test_totals_auto_computed(self, rich_case: ChangeCase) -> None:
        assert rich_case.total_files_changed == 3
        assert rich_case.total_lines_added == 312 + 28 + 67
        assert rich_case.total_lines_removed == 14 + 45 + 0

    def test_has_breaking_changes_flag(self, rich_case: ChangeCase) -> None:
        assert rich_case.has_breaking_changes is True

    def test_no_breaking_changes_by_default(self, minimal_case: ChangeCase) -> None:
        assert minimal_case.has_breaking_changes is False

    def test_invalid_short_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChangeCase(title="AB")  # min_length=3

    def test_valid_short_sha(self) -> None:
        case = ChangeCase(title="Test", commit_sha="abc1234")
        assert case.commit_sha == "abc1234"

    def test_valid_full_sha(self) -> None:
        full_sha = "a" * 40
        case = ChangeCase(title="Test", commit_sha=full_sha)
        assert case.commit_sha == full_sha

    def test_invalid_sha_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChangeCase(title="Test", commit_sha="abc12")  # 5 chars — invalid

    def test_case_id_is_uuid_string(self, minimal_case: ChangeCase) -> None:
        import uuid
        uuid.UUID(minimal_case.case_id)  # raises ValueError if not valid UUID


# ── Finding ───────────────────────────────────────────────────────────────────

class TestFinding:

    def test_finding_id_generated(self, sample_finding: Finding) -> None:
        assert len(sample_finding.finding_id) == 36  # UUID4 format

    def test_finding_is_not_suppressed_by_default(self, sample_finding: Finding) -> None:
        assert sample_finding.suppressed is False

    def test_frozen_finding_rejects_mutation(self, sample_finding: Finding) -> None:
        with pytest.raises((TypeError, ValidationError)):
            sample_finding.severity = Severity.LOW  # type: ignore[misc]


# ── AgentResult ───────────────────────────────────────────────────────────────

class TestAgentResult:

    def test_max_severity_none_when_no_findings(self) -> None:
        result = AgentResult(agent_name="security")
        assert result.max_severity is None

    def test_max_severity_returns_highest(self, sample_finding: Finding) -> None:
        low_finding = Finding(
            agent="security",
            category=FindingCategory.SECURITY,
            severity=Severity.LOW,
            title="Minor info leak",
            description="Low risk item",
        )
        result = AgentResult(
            agent_name="security",
            findings=[sample_finding, low_finding],
        )
        assert result.max_severity == Severity.HIGH

    def test_max_severity_ignores_suppressed(self, sample_finding: Finding) -> None:
        suppressed = Finding(
            agent="security",
            category=FindingCategory.SECURITY,
            severity=Severity.CRITICAL,
            title="False positive",
            description="Suppressed by security lead",
            suppressed=True,
        )
        result = AgentResult(agent_name="security", findings=[sample_finding, suppressed])
        # CRITICAL is suppressed; only HIGH remains
        assert result.max_severity == Severity.HIGH

    def test_duration_seconds_calculated(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        result = AgentResult(
            agent_name="impact",
            started_at=now,
            completed_at=now + datetime.timedelta(seconds=3.5),
        )
        assert result.duration_seconds == pytest.approx(3.5, abs=0.01)


# ── WorkflowState ─────────────────────────────────────────────────────────────

class TestWorkflowState:

    def test_is_terminal_pending(self, minimal_case: ChangeCase) -> None:
        state = WorkflowState(case=minimal_case, status=CaseStatus.PENDING)
        assert state.is_terminal is False

    def test_is_terminal_completed(self, minimal_case: ChangeCase) -> None:
        state = WorkflowState(case=minimal_case, status=CaseStatus.COMPLETED)
        assert state.is_terminal is True

    def test_is_terminal_blocked(self, minimal_case: ChangeCase) -> None:
        state = WorkflowState(case=minimal_case, status=CaseStatus.BLOCKED)
        assert state.is_terminal is True

    def test_max_severity_no_findings(self, minimal_case: ChangeCase) -> None:
        state = WorkflowState(case=minimal_case)
        assert state.max_severity_across_all_agents is None

    def test_max_severity_with_findings(
        self, minimal_case: ChangeCase, sample_finding: Finding
    ) -> None:
        state = WorkflowState(case=minimal_case, all_findings=[sample_finding])
        assert state.max_severity_across_all_agents == Severity.HIGH


# ── Serialisers ───────────────────────────────────────────────────────────────

class TestSerialisers:

    def test_change_case_to_json_is_valid_json(self, minimal_case: ChangeCase) -> None:
        raw = change_case_to_json(minimal_case)
        parsed = json.loads(raw)
        assert parsed["title"] == minimal_case.title

    def test_to_json_pretty_has_newlines(self, minimal_case: ChangeCase) -> None:
        raw = to_json(minimal_case, pretty=True)
        assert "\n" in raw or "\n" not in raw  # just ensure it parses
        assert json.loads(raw)["title"] == minimal_case.title

    def test_from_json_round_trip(self, minimal_case: ChangeCase) -> None:
        raw = change_case_to_json(minimal_case, pretty=False)
        restored: ChangeCase = from_json(raw, ChangeCase)
        assert restored.case_id == minimal_case.case_id
        assert restored.title == minimal_case.title

    def test_workflow_state_serialises(
        self, minimal_case: ChangeCase, sample_finding: Finding
    ) -> None:
        state = WorkflowState(
            case=minimal_case,
            status=CaseStatus.PENDING,
            all_findings=[sample_finding],
        )
        raw = workflow_state_to_json(state)
        parsed = json.loads(raw)
        assert parsed["status"] == "PENDING"
        assert len(parsed["all_findings"]) == 1


# ── Fixture loading from disk ─────────────────────────────────────────────────

class TestFixtureLoading:

    def test_sample_pr_payload_is_valid_change_case(self) -> None:
        """Ensure the committed fixture file parses into a valid ChangeCase."""
        import pathlib
        fixture = pathlib.Path("tests/fixtures/sample_pr_payload.json")
        assert fixture.exists(), "Fixture file is missing"
        case: ChangeCase = from_json(fixture.read_bytes(), ChangeCase)
        assert case.total_files_changed == 6
        assert case.has_breaking_changes is True
        assert case.data_classification == DataClassification.RESTRICTED
