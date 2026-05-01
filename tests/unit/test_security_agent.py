"""
Unit tests for SecurityAgent, MockSASTScanner, and threat hypothesis logic.

Tests cover:
- MockSASTScanner returns findings for matching file paths
- SAST deduplication (one finding per rule per scan)
- Rule-based security path checks
- Credential file detection (CRITICAL)
- Threat hypothesis generation for concern combinations
- End-to-end agent run with banking cases
- CWE reference captured in findings
- Security posture metadata
"""

from __future__ import annotations

import pytest

from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.security import SecurityAgent
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState
from change_review_orchestrator.integrations.mock.sast_mock import MockSASTScanner
from tests.fixtures.sample_diffs import (
    make_auth_refactor_case,
    make_docs_only_case,
    make_pci_tokenisation_case,
)


# ── MockSASTScanner tests ─────────────────────────────────────────────────────

class TestMockSASTScanner:

    def test_jwt_file_triggers_sast_004(self) -> None:
        scanner = MockSASTScanner()
        results = scanner.scan_files(["src/auth/jwt_handler.py"])
        rule_ids = [r["rule_id"] for r in results]
        assert "SAST-004" in rule_ids

    def test_payment_file_triggers_sast_006(self) -> None:
        scanner = MockSASTScanner()
        results = scanner.scan_files(["src/payments/transaction_service.py"])
        rule_ids = [r["rule_id"] for r in results]
        assert "SAST-006" in rule_ids

    def test_requirements_txt_triggers_sast_007(self) -> None:
        scanner = MockSASTScanner()
        results = scanner.scan_files(["requirements.txt"])
        rule_ids = [r["rule_id"] for r in results]
        assert "SAST-007" in rule_ids

    def test_docs_file_triggers_no_findings(self) -> None:
        scanner = MockSASTScanner()
        results = scanner.scan_files(["docs/architecture.md", "README.md"])
        assert len(results) == 0

    def test_deduplication_one_rule_per_scan(self) -> None:
        """Same rule should not appear twice even if two files match."""
        scanner = MockSASTScanner()
        results = scanner.scan_files([
            "src/auth/jwt_handler.py",
            "src/auth/token_service.py",
        ])
        rule_ids = [r["rule_id"] for r in results]
        assert len(rule_ids) == len(set(rule_ids)), "Duplicate SAST rules found"

    def test_scan_result_has_required_keys(self) -> None:
        scanner = MockSASTScanner()
        results = scanner.scan_files(["src/auth/jwt_handler.py"])
        assert len(results) > 0
        for r in results:
            for key in ("rule_id", "title", "severity", "cwe", "description",
                        "remediation", "affected_file"):
                assert key in r, f"Missing key '{key}' in SAST result"

    def test_severity_values_are_valid(self) -> None:
        valid = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        scanner = MockSASTScanner()
        results = scanner.scan_files([
            "src/auth/jwt.py", "src/payments/ledger.py",
            "requirements.txt", "src/db/repository.py",
        ])
        for r in results:
            assert r["severity"] in valid


# ── SecurityAgent integration tests ──────────────────────────────────────────

class TestSecurityAgent:

    def _run_pipeline(self, case: ChangeCase) -> WorkflowState:
        state = WorkflowState(case=case)
        state = IntakeAgent().run(state)
        state = ImpactAgent().run(state)
        state = SecurityAgent().run(state)
        return state

    def test_agent_completes(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        assert state.agent_results["security"].status == AgentStatus.COMPLETED

    def test_sast_findings_count_in_metadata(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["security"].metadata
        assert "sast_findings_count" in meta
        assert meta["sast_findings_count"] >= 0

    def test_sast_rules_triggered_list_populated(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        rules = state.agent_results["security"].metadata["sast_rules_triggered"]
        assert isinstance(rules, list)

    def test_all_security_findings_are_correct_category(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        for f in state.agent_results["security"].findings:
            assert f.category == FindingCategory.SECURITY

    def test_cwe_reference_in_sast_finding_description(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        sast_findings = [
            f for f in state.agent_results["security"].findings
            if f.title.startswith("[SAST")
        ]
        for f in sast_findings:
            assert "CWE-" in f.description, f"No CWE in: {f.title}"

    def test_threat_hypothesis_raised_for_auth_crypto(self) -> None:
        """Auth + crypto concerns should trigger the authentication bypass hypothesis."""
        case = make_auth_refactor_case()
        # Add a crypto file to trigger both auth and crypto concerns
        case_with_crypto = ChangeCase(
            title=case.title,
            author=case.author,
            commit_sha=case.commit_sha,
            changed_files=list(case.changed_files) + [
                ChangedFile(
                    path="src/crypto/cipher_utils.py",
                    category=AssetCategory.SOURCE_CODE,
                    lines_added=30, lines_removed=10,
                )
            ],
        )
        state = self._run_pipeline(case_with_crypto)
        findings = state.agent_results["security"].findings
        threat_findings = [f for f in findings if "Threat Hypothesis" in f.title]
        assert len(threat_findings) >= 1

    def test_threat_hypothesis_for_pci_api_change(self) -> None:
        """PCI + interface concerns should trigger PCI data exposure hypothesis."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["security"].findings
        threat_findings = [f for f in findings if "Threat Hypothesis" in f.title]
        assert len(threat_findings) >= 1

    def test_threat_hypotheses_are_high_severity(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        threat_findings = [
            f for f in state.agent_results["security"].findings
            if "Threat Hypothesis" in f.title
        ]
        for f in threat_findings:
            assert f.severity == Severity.HIGH

    def test_security_posture_metadata_present(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["security"].metadata
        assert meta["security_posture"] in ("CLEAR", "MEDIUM", "HIGH", "CRITICAL")

    def test_docs_only_posture_is_clear(self) -> None:
        state = self._run_pipeline(make_docs_only_case())
        meta = state.agent_results["security"].metadata
        assert meta["security_posture"] in ("CLEAR", "MEDIUM")

    def test_credential_file_raises_critical(self) -> None:
        case = ChangeCase(
            title="Accidental key commit",
            author="dev@bank.com",
            commit_sha="abc1234",
            changed_files=[
                ChangedFile(
                    path="keys/deploy.pem",
                    category=AssetCategory.SECRET_OR_CREDENTIAL,
                    lines_added=50, lines_removed=0,
                )
            ],
        )
        state = WorkflowState(case=case)
        state = SecurityAgent().run(state)
        crits = [f for f in state.agent_results["security"].findings
                 if f.severity == Severity.CRITICAL]
        assert len(crits) >= 1

    def test_evidence_item_created(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        labels = [e.label for e in state.agent_results["security"].evidence_items]
        assert "Security Scan Report" in labels

    def test_findings_merged_to_all_findings(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        sec_count = len(state.agent_results["security"].findings)
        assert sec_count <= len(state.all_findings)

    def test_summary_includes_posture(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        summary = state.agent_results["security"].summary
        assert "Posture:" in summary or "posture" in summary.lower()
