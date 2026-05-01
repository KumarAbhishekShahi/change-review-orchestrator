"""
Unit tests for IntakeAgent and its classify_file() helper.

All tests are pure-Python — no network, no DB.
The LocalFilesystemArtifactStore writes to a temp directory.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from change_review_orchestrator.agents.intake import IntakeAgent, classify_file
from change_review_orchestrator.domain.enums import (
    AssetCategory,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState
from change_review_orchestrator.config import get_settings


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_artifact_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect artifact store to a temp directory for every test."""
    monkeypatch.setattr(
        "change_review_orchestrator.config.Settings.artifact_store_path",
        property(lambda self: tmp_path),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def full_case() -> ChangeCase:
    """ChangeCase with all required metadata and diverse file types."""
    return ChangeCase(
        source_system="github",
        source_ref="PR-4821",
        repository="banking-org/payments-service",
        branch="feature/PAY-1234",
        commit_sha="a3f9e2c",
        title="PAY-1234: Add PCI tokenisation layer",
        author="jane.doe@bank.com",
        jira_ticket="PAY-1234",
        release_version="2.14.0",
        changed_files=[
            ChangedFile(path="src/payments/service.py", lines_added=50, lines_removed=5),
            ChangedFile(path="tests/unit/test_service.py", lines_added=30, lines_removed=0),
            ChangedFile(path="alembic/versions/0042_add_token.py", lines_added=20, lines_removed=0),
            ChangedFile(path="requirements.txt", lines_added=2, lines_removed=0),
            ChangedFile(path="infra/vault/policy.hcl", lines_added=10, lines_removed=0),
        ],
    )


@pytest.fixture
def minimal_case() -> ChangeCase:
    """ChangeCase missing optional but flagged metadata fields."""
    return ChangeCase(
        title="Hotfix: null pointer in payment flow",
        changed_files=[
            ChangedFile(path="src/payments/processor.py", lines_added=3, lines_removed=1),
        ],
    )


# ── classify_file() tests ─────────────────────────────────────────────────────

class TestClassifyFile:

    def test_python_source_file(self) -> None:
        assert classify_file("src/payments/service.py") == AssetCategory.SOURCE_CODE

    def test_test_file_by_prefix(self) -> None:
        assert classify_file("tests/unit/test_service.py") == AssetCategory.TEST

    def test_test_file_in_test_dir(self) -> None:
        assert classify_file("src/payments/test_processor.py") == AssetCategory.TEST

    def test_alembic_migration(self) -> None:
        assert classify_file("alembic/versions/0042_add_column.py") == AssetCategory.DATABASE_MIGRATION

    def test_terraform_file(self) -> None:
        assert classify_file("infra/terraform/main.tf") == AssetCategory.INFRASTRUCTURE_AS_CODE

    def test_hcl_file(self) -> None:
        assert classify_file("infra/vault/policy.hcl") == AssetCategory.INFRASTRUCTURE_AS_CODE

    def test_requirements_txt(self) -> None:
        assert classify_file("requirements.txt") == AssetCategory.DEPENDENCY_MANIFEST

    def test_pyproject_toml_is_dependency_manifest(self) -> None:
        assert classify_file("pyproject.toml") == AssetCategory.DEPENDENCY_MANIFEST

    def test_openapi_yaml(self) -> None:
        assert classify_file("docs/api/openapi.yaml") == AssetCategory.API_SCHEMA

    def test_github_workflow(self) -> None:
        assert classify_file(".github/workflows/ci.yml") == AssetCategory.CI_CD_PIPELINE

    def test_markdown_docs(self) -> None:
        assert classify_file("docs/architecture.md") == AssetCategory.DOCUMENTATION

    def test_private_key_is_credential(self) -> None:
        assert classify_file("keys/server.key") == AssetCategory.SECRET_OR_CREDENTIAL

    def test_pem_file_is_credential(self) -> None:
        assert classify_file("certs/server.pem") == AssetCategory.SECRET_OR_CREDENTIAL

    def test_unknown_extension(self) -> None:
        assert classify_file("some/mystery/file.xyz") == AssetCategory.UNKNOWN

    def test_java_source_file(self) -> None:
        assert classify_file("src/main/java/com/bank/Service.java") == AssetCategory.SOURCE_CODE

    def test_proto_file_is_api_schema(self) -> None:
        assert classify_file("proto/payments.proto") == AssetCategory.API_SCHEMA


# ── IntakeAgent tests ─────────────────────────────────────────────────────────

class TestIntakeAgent:

    def test_intake_completes_without_error(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        result = updated.agent_results["intake"]
        from change_review_orchestrator.domain.enums import AgentStatus
        assert result.status == AgentStatus.COMPLETED

    def test_classified_files_in_metadata(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        classified = updated.agent_results["intake"].metadata["classified_files"]
        assert len(classified) == 5

    def test_category_counts_populated(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        counts = updated.agent_results["intake"].metadata["category_counts"]
        # Should have source, test, migration, dependency, IaC
        assert len(counts) >= 4

    def test_missing_metadata_findings_raised(self, minimal_case: ChangeCase) -> None:
        """Missing author, jira_ticket, release_version, commit_sha → findings."""
        state = WorkflowState(case=minimal_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        findings = updated.agent_results["intake"].findings
        missing_titles = [f.title for f in findings]
        assert any("author" in t for t in missing_titles)
        assert any("jira_ticket" in t for t in missing_titles)

    def test_no_missing_metadata_for_complete_case(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        missing = updated.agent_results["intake"].metadata["missing_metadata_fields"]
        assert missing == []

    def test_breaking_change_finding_raised(self) -> None:
        case = ChangeCase(
            title="Breaking API change",
            author="dev@bank.com",
            commit_sha="abc1234",
            changed_files=[
                ChangedFile(
                    path="src/api/schema.py",
                    is_breaking_change=True,
                    breaking_change_reason="Field removed",
                    lines_added=5,
                    lines_removed=20,
                ),
            ],
        )
        state = WorkflowState(case=case)
        agent = IntakeAgent()
        updated = agent.run(state)
        findings = updated.agent_results["intake"].findings
        impact_findings = [f for f in findings if f.category == FindingCategory.IMPACT]
        assert len(impact_findings) >= 1
        assert impact_findings[0].severity == Severity.HIGH

    def test_credential_file_raises_critical_finding(self) -> None:
        case = ChangeCase(
            title="Accidental key commit",
            author="dev@bank.com",
            commit_sha="abc1234",
            changed_files=[
                ChangedFile(path="keys/deploy.pem", lines_added=50, lines_removed=0),
            ],
        )
        state = WorkflowState(case=case)
        agent = IntakeAgent()
        updated = agent.run(state)
        findings = updated.agent_results["intake"].findings
        crit = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(crit) >= 1
        assert "credential" in crit[0].title.lower() or "key" in crit[0].title.lower()

    def test_canonical_case_json_written_to_store(self, full_case: ChangeCase, tmp_path: Path) -> None:
        """Artefact store must contain canonical_case.json after intake."""
        # Patch the store root to tmp_path
        from unittest.mock import patch
        with patch(
            "change_review_orchestrator.integrations.mock.artifact_store_mock.get_settings"
        ) as mock_settings:
            mock_settings.return_value.artifact_store_path = tmp_path
            state = WorkflowState(case=full_case)
            agent = IntakeAgent()
            updated = agent.run(state)

        artifact_path = tmp_path / full_case.case_id / "canonical_case.json"
        assert artifact_path.exists()
        data = json.loads(artifact_path.read_text())
        assert data["case_id"] == full_case.case_id

    def test_evidence_items_created(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        evidence = updated.agent_results["intake"].evidence_items
        labels = [e.label for e in evidence]
        assert "Canonical Case File" in labels
        assert "Canonical Case JSON" in labels

    def test_findings_merged_into_state_all_findings(self, minimal_case: ChangeCase) -> None:
        """BaseAgent.run() must merge agent findings into state.all_findings."""
        state = WorkflowState(case=minimal_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        assert len(updated.all_findings) == len(updated.agent_results["intake"].findings)

    def test_summary_is_non_empty(self, full_case: ChangeCase) -> None:
        state = WorkflowState(case=full_case)
        agent = IntakeAgent()
        updated = agent.run(state)
        assert updated.agent_results["intake"].summary != ""
