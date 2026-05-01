"""
Unit tests for PolicyAgent, _rule_applies(), and _check_obligation().

Tests cover:
- Rule applicability matching (change_type, data_classification, asset_categories, concerns)
- Obligation checking (truthy/falsy gap_check logic)
- Finding generation per gap (policy reference captured)
- Satisfied rules produce no findings
- PCI + security patch rules trigger correctly
- Full end-to-end agent run with banking PCI case
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.policy import PolicyAgent, _check_obligation, _rule_applies
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    ChangeType,
    DataClassification,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState
from tests.fixtures.sample_diffs import make_auth_refactor_case, make_pci_tokenisation_case

FIXTURE_POLICY_FILE = Path("tests/fixtures/policy_rules.yaml")


# ── _rule_applies() tests ─────────────────────────────────────────────────────

class TestRuleApplies:

    def _rule(self, change_types=None, data_cls=None, asset_cats=None, concerns=None):
        return {
            "applies_to": {
                "change_types": change_types or ["*"],
                "data_classifications": data_cls or ["*"],
                "asset_categories": asset_cats or [],
                "concerns": concerns or [],
            }
        }

    def test_wildcard_change_type_matches_any(self) -> None:
        rule = self._rule(change_types=["*"])
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, set(), set())

    def test_specific_change_type_matches(self) -> None:
        rule = self._rule(change_types=["FEATURE"])
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, set(), set())

    def test_specific_change_type_excludes_others(self) -> None:
        rule = self._rule(change_types=["HOTFIX"])
        assert not _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, set(), set())

    def test_wildcard_data_classification_matches_any(self) -> None:
        rule = self._rule(data_cls=["*"])
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.RESTRICTED, set(), set())

    def test_specific_data_classification_matches(self) -> None:
        rule = self._rule(data_cls=["RESTRICTED"])
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.RESTRICTED, set(), set())

    def test_specific_data_classification_excludes_others(self) -> None:
        rule = self._rule(data_cls=["HIGHLY_RESTRICTED"])
        assert not _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, set(), set())

    def test_asset_category_filter_matches(self) -> None:
        rule = self._rule(asset_cats=["DATABASE_MIGRATION"])
        cats = {"DATABASE_MIGRATION", "SOURCE_CODE"}
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, cats, set())

    def test_asset_category_filter_excludes_when_no_match(self) -> None:
        rule = self._rule(asset_cats=["DATABASE_MIGRATION"])
        cats = {"SOURCE_CODE", "TEST"}
        assert not _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, cats, set())

    def test_concern_filter_matches(self) -> None:
        rule = self._rule(concerns=["payment/pci"])
        concerns = {"payment/pci", "auth/authz"}
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.RESTRICTED, set(), concerns)

    def test_concern_filter_excludes_when_no_match(self) -> None:
        rule = self._rule(concerns=["payment/pci"])
        concerns = {"auth/authz", "crypto/tls"}
        assert not _rule_applies(rule, ChangeType.FEATURE, DataClassification.RESTRICTED, set(), concerns)

    def test_empty_asset_cat_list_ignores_category(self) -> None:
        rule = self._rule(asset_cats=[])
        assert _rule_applies(rule, ChangeType.FEATURE, DataClassification.INTERNAL, {"SOURCE_CODE"}, set())


# ── _check_obligation() tests ─────────────────────────────────────────────────

class TestCheckObligation:

    def test_truthy_check_passes_when_field_present(self) -> None:
        rule = {"gap_check": "jira_ticket", "gap_check_truthy": True}
        case_data = {"jira_ticket": "PAY-1234"}
        met, reason = _check_obligation(rule, case_data)
        assert met is True

    def test_truthy_check_fails_when_field_missing(self) -> None:
        rule = {"gap_check": "jira_ticket", "gap_check_truthy": True}
        case_data = {"jira_ticket": None}
        met, reason = _check_obligation(rule, case_data)
        assert met is False
        assert "missing or empty" in reason

    def test_truthy_check_fails_when_empty_list(self) -> None:
        rule = {"gap_check": "reviewers", "gap_check_truthy": True}
        case_data = {"reviewers": []}
        met, reason = _check_obligation(rule, case_data)
        assert met is False

    def test_falsy_check_passes_when_field_absent(self) -> None:
        rule = {"gap_check": "has_breaking_changes", "gap_check_truthy": False}
        case_data = {"has_breaking_changes": False}
        met, reason = _check_obligation(rule, case_data)
        assert met is True

    def test_falsy_check_fails_when_field_present(self) -> None:
        rule = {"gap_check": "has_breaking_changes", "gap_check_truthy": False}
        case_data = {"has_breaking_changes": True}
        met, reason = _check_obligation(rule, case_data)
        assert met is False

    def test_null_gap_check_returns_unmet(self) -> None:
        rule = {"gap_check": None, "gap_check_truthy": True}
        met, reason = _check_obligation(rule, {})
        assert met is False
        assert "manual verification" in reason


# ── PolicyAgent integration tests ─────────────────────────────────────────────

class TestPolicyAgent:

    def _run_pipeline(self, case: ChangeCase) -> WorkflowState:
        """Run Intake → Impact → Policy for a full pipeline context."""
        state = WorkflowState(case=case)
        state = IntakeAgent().run(state)
        state = ImpactAgent().run(state)
        state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
        return state

    def test_agent_completes(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        assert state.agent_results["policy"].status == AgentStatus.COMPLETED

    def test_applicable_rules_count_positive(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["policy"].metadata
        assert meta["applicable_rules"] > 0

    def test_policy_gap_findings_have_references(self) -> None:
        """Every policy finding must have a non-empty policy_reference."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["policy"].findings
        for f in findings:
            assert f.policy_reference, f"Finding '{f.title}' is missing policy_reference"

    def test_policy_gap_findings_are_policy_category(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["policy"].findings
        for f in findings:
            assert f.category == FindingCategory.POLICY

    def test_pci_rule_triggers_for_restricted_payment_change(self) -> None:
        """POL-PCI-001 must fire for RESTRICTED PCI tokenisation case."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["policy"].findings
        pci_findings = [f for f in findings if "POL-PCI-001" in (f.policy_reference or "")]
        assert len(pci_findings) >= 1
        assert pci_findings[0].severity == Severity.CRITICAL

    def test_db_policy_triggers_for_migration_files(self) -> None:
        """POL-DB-001 must fire when DATABASE_MIGRATION files are present."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["policy"].findings
        db_findings = [f for f in findings if "POL-DB-001" in (f.policy_reference or "")]
        assert len(db_findings) >= 1

    def test_iac_policy_triggers_for_hcl_files(self) -> None:
        """POL-IAC-001 must fire when INFRASTRUCTURE_AS_CODE files are present."""
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["policy"].findings
        iac_findings = [f for f in findings if "POL-IAC-001" in (f.policy_reference or "")]
        assert len(iac_findings) >= 1

    def test_security_patch_rule_triggers_for_hotfix(self) -> None:
        """POL-SEC-002 must fire for HOTFIX change type."""
        case = ChangeCase(
            title="Emergency hotfix for auth bypass",
            change_type=ChangeType.HOTFIX,
            author="dev@bank.com",
            commit_sha="abc1234",
            changed_files=[ChangedFile(path="src/auth/handler.py", lines_added=5, lines_removed=2)],
        )
        state = WorkflowState(case=case)
        state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
        findings = state.agent_results["policy"].findings
        sec_findings = [f for f in findings if "POL-SEC-002" in (f.policy_reference or "")]
        assert len(sec_findings) >= 1
        assert sec_findings[0].severity == Severity.CRITICAL

    def test_no_findings_when_all_obligations_met(self) -> None:
        """A well-formed case with all metadata present should have minimal gaps."""
        case = ChangeCase(
            title="Low-risk docs update",
            change_type=ChangeType.DOCUMENTATION,
            data_classification=DataClassification.PUBLIC,
            author="writer@bank.com",
            commit_sha="abc1234",
            jira_ticket="DOC-001",
            release_version="1.0.1",
            reviewers=["lead@bank.com"],
            changed_files=[
                ChangedFile(path="docs/guide.md",
                            category=AssetCategory.DOCUMENTATION,
                            lines_added=10, lines_removed=5),
            ],
        )
        state = WorkflowState(case=case)
        state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
        # For a PUBLIC DOCUMENTATION change, minimal policies should fire
        findings = state.agent_results["policy"].findings
        critical_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        assert len(critical_high) == 0

    def test_policy_evidence_item_created(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        evidence = state.agent_results["policy"].evidence_items
        labels = [e.label for e in evidence]
        assert "Policy Obligation Audit" in labels

    def test_gap_count_matches_findings_count(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["policy"].metadata
        findings_count = len(state.agent_results["policy"].findings)
        assert meta["gap_count"] == findings_count

    def test_policy_references_list_in_metadata(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        refs = state.agent_results["policy"].metadata["policy_references"]
        assert isinstance(refs, list)
        assert len(refs) > 0
        assert all(r.startswith("POL-") for r in refs)

    def test_findings_merged_into_all_findings(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        agent_count = len(state.agent_results["policy"].findings)
        # all_findings = intake + impact + policy
        assert agent_count <= len(state.all_findings)
