"""
Unit tests for ImpactAgent and its scoring/classification helpers.

Tests cover:
- Per-file risk scoring (_score_file)
- Tier assignment (_tier_from_score)
- Category-specific structural findings (DB, IaC, API schema, deps, pipeline)
- Impact graph metadata structure
- End-to-end agent run with realistic banking cases
"""

from __future__ import annotations

import pytest

from change_review_orchestrator.agents.impact import (
    ImpactAgent,
    _score_file,
    _tier_from_score,
)
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState
from tests.fixtures.sample_diffs import (
    make_auth_refactor_case,
    make_docs_only_case,
    make_pci_tokenisation_case,
    make_pipeline_change_case,
)


# ── _score_file() tests ───────────────────────────────────────────────────────

class TestScoreFile:

    def test_auth_file_scores_high(self) -> None:
        cf = ChangedFile(path="src/auth/jwt_handler.py", category=AssetCategory.SOURCE_CODE,
                         lines_added=50, lines_removed=20)
        score, concerns = _score_file(cf)
        assert score >= 45
        assert "auth/authz" in concerns

    def test_crypto_file_scores_high(self) -> None:
        cf = ChangedFile(path="src/crypto/cipher_utils.py", category=AssetCategory.SOURCE_CODE,
                         lines_added=30, lines_removed=10)
        score, concerns = _score_file(cf)
        assert score >= 45
        assert "crypto/tls" in concerns

    def test_payment_file_scores_high(self) -> None:
        cf = ChangedFile(path="src/payments/ledger.py", category=AssetCategory.SOURCE_CODE,
                         lines_added=100, lines_removed=50)
        score, concerns = _score_file(cf)
        assert score >= 45
        assert "payment/pci" in concerns

    def test_db_migration_scores_high(self) -> None:
        cf = ChangedFile(path="alembic/versions/0042_add_col.py",
                         category=AssetCategory.DATABASE_MIGRATION,
                         lines_added=40, lines_removed=0)
        score, concerns = _score_file(cf)
        assert score >= 45

    def test_api_schema_scores_critical(self) -> None:
        cf = ChangedFile(path="src/payments/schema/payment.py",
                         category=AssetCategory.API_SCHEMA,
                         lines_added=28, lines_removed=45,
                         is_breaking_change=True)
        score, concerns = _score_file(cf)
        assert score >= 70
        assert "breaking-change" in concerns

    def test_docs_only_scores_low(self) -> None:
        cf = ChangedFile(path="docs/README.md", category=AssetCategory.DOCUMENTATION,
                         lines_added=10, lines_removed=5)
        score, concerns = _score_file(cf)
        assert score < 20

    def test_test_file_scores_low(self) -> None:
        cf = ChangedFile(path="tests/unit/test_service.py", category=AssetCategory.TEST,
                         lines_added=50, lines_removed=5)
        score, concerns = _score_file(cf)
        assert score < 30

    def test_breaking_change_adds_score(self) -> None:
        cf_base = ChangedFile(path="src/api.py", category=AssetCategory.SOURCE_CODE,
                              lines_added=5, lines_removed=5, is_breaking_change=False)
        cf_breaking = ChangedFile(path="src/api.py", category=AssetCategory.SOURCE_CODE,
                                  lines_added=5, lines_removed=5, is_breaking_change=True)
        score_base, _ = _score_file(cf_base)
        score_breaking, _ = _score_file(cf_breaking)
        assert score_breaking > score_base

    def test_high_churn_adds_score(self) -> None:
        cf_small = ChangedFile(path="src/service.py", category=AssetCategory.SOURCE_CODE,
                               lines_added=5, lines_removed=5)
        cf_large = ChangedFile(path="src/service.py", category=AssetCategory.SOURCE_CODE,
                               lines_added=500, lines_removed=400)
        score_small, _ = _score_file(cf_small)
        score_large, _ = _score_file(cf_large)
        assert score_large > score_small

    def test_score_capped_at_100(self) -> None:
        """Score must never exceed 100 even for worst-case files."""
        cf = ChangedFile(
            path="src/auth/crypto/payment/schema/jwt_token.py",
            category=AssetCategory.API_SCHEMA,
            lines_added=1000, lines_removed=1000,
            is_breaking_change=True,
        )
        score, _ = _score_file(cf)
        assert score <= 100


# ── _tier_from_score() tests ──────────────────────────────────────────────────

class TestTierFromScore:

    def test_critical_tier(self) -> None:
        assert _tier_from_score(70) == "critical"
        assert _tier_from_score(100) == "critical"

    def test_high_tier(self) -> None:
        assert _tier_from_score(45) == "high"
        assert _tier_from_score(69) == "high"

    def test_medium_tier(self) -> None:
        assert _tier_from_score(20) == "medium"
        assert _tier_from_score(44) == "medium"

    def test_low_tier(self) -> None:
        assert _tier_from_score(0) == "low"
        assert _tier_from_score(19) == "low"


# ── ImpactAgent integration tests ─────────────────────────────────────────────

class TestImpactAgent:

    def _run(self, case: ChangeCase) -> WorkflowState:
        state = WorkflowState(case=case)
        agent = ImpactAgent()
        return agent.run(state)

    def test_agent_completes(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        assert updated.agent_results["impact"].status == AgentStatus.COMPLETED

    def test_impact_graph_populated(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        graph = updated.agent_results["impact"].metadata["impact_graph"]
        assert len(graph) == 6  # one node per changed file

    def test_tier_counts_sum_to_file_count(self) -> None:
        case = make_pci_tokenisation_case()
        updated = self._run(case)
        tiers = updated.agent_results["impact"].metadata["tier_counts"]
        total = sum(tiers.values())
        assert total == len(case.changed_files)

    def test_db_migration_finding_raised(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        findings = updated.agent_results["impact"].findings
        db_findings = [f for f in findings if "Database migration" in f.title]
        assert len(db_findings) >= 1
        assert db_findings[0].severity == Severity.HIGH

    def test_iac_finding_raised(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        findings = updated.agent_results["impact"].findings
        iac_findings = [f for f in findings if "Infrastructure-as-Code" in f.title]
        assert len(iac_findings) >= 1
        assert iac_findings[0].severity == Severity.HIGH

    def test_api_schema_finding_raised(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        findings = updated.agent_results["impact"].findings
        schema_findings = [f for f in findings if "API schema" in f.title]
        assert len(schema_findings) >= 1

    def test_dependency_finding_raised(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        findings = updated.agent_results["impact"].findings
        dep_findings = [f for f in findings if "Dependency" in f.title]
        assert len(dep_findings) >= 1
        assert dep_findings[0].severity == Severity.MEDIUM

    def test_pipeline_finding_raised(self) -> None:
        updated = self._run(make_pipeline_change_case())
        findings = updated.agent_results["impact"].findings
        pipe_findings = [f for f in findings if "CI/CD pipeline" in f.title]
        assert len(pipe_findings) >= 1

    def test_docs_only_produces_no_high_findings(self) -> None:
        updated = self._run(make_docs_only_case())
        findings = updated.agent_results["impact"].findings
        high_or_above = [
            f for f in findings
            if f.severity in (Severity.HIGH, Severity.CRITICAL)
        ]
        assert len(high_or_above) == 0

    def test_auth_refactor_detected_as_high_risk(self) -> None:
        updated = self._run(make_auth_refactor_case())
        meta = updated.agent_results["impact"].metadata
        concerns = meta["all_concerns"]
        assert "auth/authz" in concerns

    def test_evidence_item_created(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        evidence = updated.agent_results["impact"].evidence_items
        labels = [e.label for e in evidence]
        assert "Impact Graph" in labels

    def test_overall_severity_in_metadata(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        meta = updated.agent_results["impact"].metadata
        assert "overall_severity" in meta
        assert meta["overall_severity"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_findings_merged_into_all_findings(self) -> None:
        """BaseAgent.run() must merge impact findings into state.all_findings."""
        updated = self._run(make_pci_tokenisation_case())
        agent_finding_count = len(updated.agent_results["impact"].findings)
        assert len(updated.all_findings) == agent_finding_count

    def test_summary_non_empty(self) -> None:
        updated = self._run(make_pci_tokenisation_case())
        assert updated.agent_results["impact"].summary != ""
