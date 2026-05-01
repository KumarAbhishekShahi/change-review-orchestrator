"""
Unit tests for TestStrategyAgent, obligation mapping, and confidence scoring.

Tests cover:
- Obligation matrix per AssetCategory
- Concern-based extra obligation injection
- Test file presence detection heuristic
- Confidence scoring (deductions per missing type)
- Gap findings raised for correct severities
- SCA/IaC label-based presence inference
- End-to-end with realistic banking cases
"""

from __future__ import annotations

import pytest

from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.test_strategy import (
    TestStrategyAgent,
    _compute_confidence,
    _detect_test_presence,
    _CATEGORY_OBLIGATIONS,
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
)


# ── Obligation matrix tests ───────────────────────────────────────────────────

class TestObligationMatrix:

    def test_source_code_requires_unit(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.SOURCE_CODE]
        assert "unit" in obl.required

    def test_api_schema_requires_contract_and_integration(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.API_SCHEMA]
        assert "contract" in obl.required
        assert "integration" in obl.required

    def test_db_migration_requires_rollback_and_integration(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.DATABASE_MIGRATION]
        assert "migration_rollback" in obl.required
        assert "integration" in obl.required

    def test_iac_requires_plan_dry_run(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.INFRASTRUCTURE_AS_CODE]
        assert "plan_dry_run" in obl.required

    def test_dependency_requires_sca_scan(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.DEPENDENCY_MANIFEST]
        assert "sca_scan" in obl.required

    def test_test_file_has_no_obligations(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.TEST]
        assert len(obl.required) == 0

    def test_documentation_has_no_obligations(self) -> None:
        obl = _CATEGORY_OBLIGATIONS[AssetCategory.DOCUMENTATION]
        assert len(obl.required) == 0


# ── _detect_test_presence() tests ─────────────────────────────────────────────

class TestDetectTestPresence:

    def _make_files(self, paths: list[str]) -> list[ChangedFile]:
        return [
            ChangedFile(
                path=p,
                category=AssetCategory.TEST if "test" in p.lower() else AssetCategory.SOURCE_CODE,
                lines_added=10,
                lines_removed=0,
            )
            for p in paths
        ]

    def test_detects_test_prefix_match(self) -> None:
        files = self._make_files([
            "src/payments/service.py",
            "tests/unit/test_service.py",
        ])
        assert _detect_test_presence(files, "src/payments/service.py") is True

    def test_no_test_file_returns_false(self) -> None:
        files = self._make_files(["src/payments/service.py"])
        assert _detect_test_presence(files, "src/payments/service.py") is False

    def test_unrelated_test_does_not_match(self) -> None:
        files = self._make_files([
            "src/payments/service.py",
            "tests/unit/test_ledger.py",
        ])
        assert _detect_test_presence(files, "src/payments/service.py") is False


# ── _compute_confidence() tests ───────────────────────────────────────────────

class TestComputeConfidence:

    def test_full_coverage_scores_100(self) -> None:
        score = _compute_confidence(
            required=frozenset({"unit", "integration"}),
            missing=frozenset(),
            has_test_file=True,
        )
        assert score == 100

    def test_missing_unit_deducts_25(self) -> None:
        score = _compute_confidence(
            required=frozenset({"unit"}),
            missing=frozenset({"unit"}),
            has_test_file=False,
        )
        # Deducts 25 for missing unit + 10 for no test file
        assert score == 65

    def test_score_capped_at_zero(self) -> None:
        score = _compute_confidence(
            required=frozenset({"unit", "integration", "contract",
                                 "security_scan", "migration_rollback"}),
            missing=frozenset({"unit", "integration", "contract",
                                "security_scan", "migration_rollback"}),
            has_test_file=False,
        )
        assert score == 0

    def test_no_test_file_deducts_extra(self) -> None:
        with_file = _compute_confidence(
            required=frozenset({"unit"}),
            missing=frozenset(),
            has_test_file=True,
        )
        without_file = _compute_confidence(
            required=frozenset({"unit"}),
            missing=frozenset(),
            has_test_file=False,
        )
        assert without_file < with_file

    def test_no_obligations_scores_100(self) -> None:
        score = _compute_confidence(
            required=frozenset(),
            missing=frozenset(),
            has_test_file=False,
        )
        assert score == 100


# ── TestStrategyAgent integration tests ──────────────────────────────────────

class TestTestStrategyAgent:

    def _run_pipeline(self, case: ChangeCase) -> WorkflowState:
        state = WorkflowState(case=case)
        state = IntakeAgent().run(state)
        state = ImpactAgent().run(state)
        state = TestStrategyAgent().run(state)
        return state

    def test_agent_completes(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        assert state.agent_results["test_strategy"].status == AgentStatus.COMPLETED

    def test_component_results_count_matches_files(self) -> None:
        case = make_pci_tokenisation_case()
        state = self._run_pipeline(case)
        comps = state.agent_results["test_strategy"].metadata["component_results"]
        assert len(comps) == len(case.changed_files)

    def test_overall_confidence_is_0_to_100(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        score = state.agent_results["test_strategy"].metadata["overall_confidence_score"]
        assert 0 <= score <= 100

    def test_db_migration_gap_raises_finding(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["test_strategy"].findings
        db_gaps = [f for f in findings if "migration" in f.title.lower()
                   or "migration_rollback" in f.description.lower()]
        assert len(db_gaps) >= 1

    def test_iac_gap_raises_finding(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["test_strategy"].findings
        iac_gaps = [f for f in findings if "vault" in f.title.lower()
                    or "plan_dry_run" in f.description.lower()]
        assert len(iac_gaps) >= 1

    def test_docs_only_has_no_gaps(self) -> None:
        state = self._run_pipeline(make_docs_only_case())
        findings = state.agent_results["test_strategy"].findings
        assert len(findings) == 0

    def test_all_gap_findings_are_test_strategy_category(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        for f in state.agent_results["test_strategy"].findings:
            assert f.category == FindingCategory.TEST_STRATEGY

    def test_concern_based_extra_obligations_injected(self) -> None:
        """PCI concern should inject security_scan + integration + regression obligations."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        comps = state.agent_results["test_strategy"].metadata["component_results"]
        # Source code file for tokenisation service should have payment/pci injected obligations
        tokenisation_comp = next(
            (c for c in comps if "tokenisation" in c["path"]), None
        )
        assert tokenisation_comp is not None
        # security_scan should be in required (injected by payment/pci concern)
        assert "security_scan" in tokenisation_comp["required_types"]

    def test_gap_count_matches_findings(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["test_strategy"].metadata
        assert meta["gap_count"] == len(state.agent_results["test_strategy"].findings)

    def test_all_missing_types_list_populated(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        missing = state.agent_results["test_strategy"].metadata["all_missing_test_types"]
        assert isinstance(missing, list)
        # PCI case should be missing multiple types
        assert len(missing) > 0

    def test_evidence_item_created(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        labels = [e.label for e in state.agent_results["test_strategy"].evidence_items]
        assert "Test Strategy Matrix" in labels

    def test_sca_scan_inferred_from_pr_label(self) -> None:
        """If PR has 'sca-scan' label, sca_scan should be present, not missing."""
        case = ChangeCase(
            title="Update dependencies with SCA scan",
            author="dev@bank.com",
            commit_sha="abc1234",
            labels=["sca-scan"],
            changed_files=[
                ChangedFile(
                    path="requirements.txt",
                    category=AssetCategory.DEPENDENCY_MANIFEST,
                    lines_added=2, lines_removed=0,
                )
            ],
        )
        state = WorkflowState(case=case)
        state = TestStrategyAgent().run(state)
        comps = state.agent_results["test_strategy"].metadata["component_results"]
        req_comp = comps[0]
        assert "sca_scan" not in req_comp["missing_types"]

    def test_summary_includes_confidence_score(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        summary = state.agent_results["test_strategy"].summary
        assert "confidence" in summary.lower()

    def test_findings_merged_to_all_findings(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        ts_count = len(state.agent_results["test_strategy"].findings)
        assert ts_count <= len(state.all_findings)
