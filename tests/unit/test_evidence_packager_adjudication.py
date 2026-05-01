"""
Unit tests for EvidencePackagerAgent and AdjudicationAgent.

Tests cover:
- Markdown report generation (sections, tables, findings)
- JSON audit bundle structure and required keys
- Artefact persistence (files written to disk)
- Composite score computation per dimension
- Escalation rule triggering (ESC-001 to ESC-005)
- Recommendation derivation (APPROVE / APPROVE_WITH_CONDITIONS / NEEDS_WORK / REJECT)
- Required vs advisory action lists
- End-to-end full pipeline run
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from change_review_orchestrator.agents.adjudication import (
    AdjudicationAgent,
    _apply_escalation_rules,
    _compute_finding_deduction,
    _compute_policy_deduction,
    _compute_reliability_deduction,
    _compute_security_deduction,
    _compute_test_deduction,
    _derive_recommendation,
)
from change_review_orchestrator.agents.evidence_packager import EvidencePackagerAgent
from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.policy import PolicyAgent
from change_review_orchestrator.agents.reliability import ReliabilityAgent
from change_review_orchestrator.agents.security import SecurityAgent
from change_review_orchestrator.agents.test_strategy import TestStrategyAgent
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    ChangeType,
    DataClassification,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, Finding, WorkflowState
from tests.fixtures.sample_diffs import make_docs_only_case, make_pci_tokenisation_case

FIXTURE_POLICY_FILE = Path("tests/fixtures/policy_rules.yaml")


def _run_full_pipeline(case: ChangeCase) -> WorkflowState:
    """Run all 8 agents to produce a fully populated state."""
    state = WorkflowState(case=case)
    state = IntakeAgent().run(state)
    state = ImpactAgent().run(state)
    state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
    state = SecurityAgent().run(state)
    state = TestStrategyAgent().run(state)
    state = ReliabilityAgent().run(state)
    state = EvidencePackagerAgent().run(state)
    state = AdjudicationAgent().run(state)
    return state


# ── Scoring function unit tests ────────────────────────────────────────────────

class TestScoringFunctions:

    def _make_finding(self, sev: Severity, suppressed: bool = False) -> Finding:
        return Finding(
            agent="test", category=FindingCategory.SECURITY,
            severity=sev, title="test", description="test",
            suppressed=suppressed,
        )

    def test_finding_deduction_critical_dominates(self) -> None:
        findings = [self._make_finding(Severity.CRITICAL) for _ in range(3)]
        d = _compute_finding_deduction(findings)
        assert d > 0
        assert d <= 35.0

    def test_suppressed_findings_not_counted(self) -> None:
        findings = [self._make_finding(Severity.CRITICAL, suppressed=True)]
        d = _compute_finding_deduction(findings)
        assert d == 0.0

    def test_finding_deduction_capped_at_35(self) -> None:
        findings = [self._make_finding(Severity.CRITICAL) for _ in range(10)]
        d = _compute_finding_deduction(findings)
        assert d == 35.0

    def test_policy_deduction_critical_gap(self) -> None:
        meta = {"gaps": [{"severity": "CRITICAL"}, {"severity": "HIGH"}]}
        d = _compute_policy_deduction(meta)
        assert d == 16.0

    def test_policy_deduction_capped_at_20(self) -> None:
        meta = {"gaps": [{"severity": "CRITICAL"}] * 5}
        d = _compute_policy_deduction(meta)
        assert d == 20.0

    def test_security_deduction_critical_posture(self) -> None:
        d = _compute_security_deduction({"security_posture": "CRITICAL"})
        assert d == 20.0

    def test_security_deduction_clear_posture(self) -> None:
        d = _compute_security_deduction({"security_posture": "CLEAR"})
        assert d == 0.0

    def test_test_deduction_zero_confidence(self) -> None:
        d = _compute_test_deduction({"overall_confidence_score": 0})
        assert d == 15.0

    def test_test_deduction_full_confidence(self) -> None:
        d = _compute_test_deduction({"overall_confidence_score": 100})
        assert d == 0.0

    def test_reliability_deduction_nonviable_rollback(self) -> None:
        d = _compute_reliability_deduction({
            "deployment_risk_score": 80,
            "rollback_viable": False,
        })
        assert d > 0

    def test_reliability_deduction_capped(self) -> None:
        d = _compute_reliability_deduction({
            "deployment_risk_score": 100,
            "rollback_viable": False,
        })
        assert d <= 10.0


# ── Escalation rule tests ─────────────────────────────────────────────────────

class TestEscalationRules:

    def _make_finding(self, sev: Severity) -> Finding:
        return Finding(
            agent="test", category=FindingCategory.SECURITY,
            severity=sev, title="test finding", description="d",
        )

    def test_esc_001_triggers_on_critical_finding(self) -> None:
        findings = [self._make_finding(Severity.CRITICAL)]
        rules = _apply_escalation_rules(findings, {}, {}, {}, {})
        esc001 = next(r for r in rules if r.rule_id == "ESC-001")
        assert esc001.triggered is True

    def test_esc_001_not_triggered_without_critical(self) -> None:
        findings = [self._make_finding(Severity.HIGH)]
        rules = _apply_escalation_rules(findings, {}, {}, {}, {})
        esc001 = next(r for r in rules if r.rule_id == "ESC-001")
        assert esc001.triggered is False

    def test_esc_002_triggers_on_pci_gap(self) -> None:
        policy_meta = {"gaps": [{"rule_id": "POL-PCI-001", "severity": "CRITICAL"}]}
        rules = _apply_escalation_rules([], policy_meta, {}, {}, {})
        esc002 = next(r for r in rules if r.rule_id == "ESC-002")
        assert esc002.triggered is True

    def test_esc_003_triggers_on_nonviable_rollback(self) -> None:
        rel_meta = {"rollback_viable": False, "rollback_blockers": ["DB migration present"]}
        rules = _apply_escalation_rules([], {}, {}, rel_meta, {})
        esc003 = next(r for r in rules if r.rule_id == "ESC-003")
        assert esc003.triggered is True

    def test_esc_004_triggers_on_critical_security_posture(self) -> None:
        security_meta = {"security_posture": "CRITICAL"}
        rules = _apply_escalation_rules([], {}, security_meta, {}, {})
        esc004 = next(r for r in rules if r.rule_id == "ESC-004")
        assert esc004.triggered is True

    def test_esc_005_triggers_on_low_test_confidence(self) -> None:
        ts_meta = {"overall_confidence_score": 10}
        rules = _apply_escalation_rules([], {}, {}, {}, ts_meta)
        esc005 = next(r for r in rules if r.rule_id == "ESC-005")
        assert esc005.triggered is True


# ── Recommendation derivation tests ──────────────────────────────────────────

class TestDeriveRecommendation:

    def _no_rules(self):
        from change_review_orchestrator.agents.adjudication import EscalationRule
        return []

    def _crit_rule(self):
        from change_review_orchestrator.agents.adjudication import EscalationRule
        return [EscalationRule("ESC-001", "critical", True, Severity.CRITICAL, "fix it")]

    def _high_rule(self):
        from change_review_orchestrator.agents.adjudication import EscalationRule
        return [EscalationRule("ESC-003", "high", True, Severity.HIGH, "fix rollback")]

    def test_approve_on_high_score_no_escalations(self) -> None:
        rec, _ = _derive_recommendation(85, self._no_rules())
        assert rec == "APPROVE"

    def test_approve_with_conditions_on_score_65(self) -> None:
        rec, _ = _derive_recommendation(65, self._no_rules())
        assert rec == "APPROVE_WITH_CONDITIONS"

    def test_needs_work_on_score_50(self) -> None:
        rec, _ = _derive_recommendation(50, self._no_rules())
        assert rec == "NEEDS_WORK"

    def test_reject_on_low_score(self) -> None:
        rec, _ = _derive_recommendation(30, self._no_rules())
        assert rec == "REJECT"

    def test_reject_on_critical_escalation_regardless_of_score(self) -> None:
        rec, _ = _derive_recommendation(90, self._crit_rule())
        assert rec == "REJECT"

    def test_needs_work_on_high_escalation(self) -> None:
        rec, _ = _derive_recommendation(75, self._high_rule())
        assert rec == "NEEDS_WORK"


# ── End-to-end full pipeline tests ───────────────────────────────────────────

class TestFullPipeline:

    def test_full_pipeline_completes(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        for agent in ("intake", "impact", "policy", "security",
                      "test_strategy", "reliability", "evidence_packager", "adjudication"):
            assert state.agent_results[agent].status == AgentStatus.COMPLETED

    def test_pci_case_recommendation_is_reject_or_needs_work(self) -> None:
        """PCI case with many unresolved gaps should not be APPROVE."""
        state = _run_full_pipeline(make_pci_tokenisation_case())
        rec = state.agent_results["adjudication"].metadata["recommendation"]
        assert rec in ("REJECT", "NEEDS_WORK")

    def test_docs_case_recommendation_is_approve(self) -> None:
        state = _run_full_pipeline(make_docs_only_case())
        rec = state.agent_results["adjudication"].metadata["recommendation"]
        assert rec in ("APPROVE", "APPROVE_WITH_CONDITIONS")

    def test_composite_score_is_0_to_100(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        score = state.agent_results["adjudication"].metadata["composite_score"]
        assert 0 <= score <= 100

    def test_required_actions_present_for_pci_case(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        actions = state.agent_results["adjudication"].metadata["required_actions"]
        assert len(actions) > 0

    def test_markdown_report_generated(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        report_path = state.agent_results["evidence_packager"].metadata["report_path"]
        assert report_path is not None
        assert Path(report_path).exists()
        content = Path(report_path).read_text()
        assert "# Change Review Report" in content
        assert "## Findings" in content
        assert "## Deployment Readiness" in content

    def test_json_audit_bundle_valid(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        bundle_path = state.agent_results["evidence_packager"].metadata["bundle_path"]
        assert bundle_path is not None
        bundle = json.loads(Path(bundle_path).read_text())
        for key in ("case_id", "findings", "finding_counts",
                    "agent_results", "evidence_index"):
            assert key in bundle, f"Missing key in audit bundle: {key}"

    def test_bundle_finding_counts_match_state(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        bundle_path = state.agent_results["evidence_packager"].metadata["bundle_path"]
        bundle = json.loads(Path(bundle_path).read_text())
        assert bundle["finding_counts"]["total"] == len(state.all_findings)

    def test_dimension_deductions_all_present(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        dims = state.agent_results["adjudication"].metadata["dimension_deductions"]
        for key in ("findings", "policy", "security", "test", "reliability"):
            assert key in dims

    def test_adjudication_evidence_item_created(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        labels = [e.label for e in state.agent_results["adjudication"].evidence_items]
        assert "Adjudication Decision" in labels
