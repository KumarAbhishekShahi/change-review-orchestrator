"""
Unit tests for ReliabilityAgent and its sub-assessors.

Tests cover:
- Deployment risk scoring per change type and tier distribution
- Rollback viability (blockers vs warnings)
- Observability signal detection from labels/description
- Blast radius scoring
- Deployment strategy recommendation logic
- End-to-end agent run with banking cases
- Finding severity correctness per scenario
"""

from __future__ import annotations

import pytest

from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.reliability import (
    ReliabilityAgent,
    _assess_blast_radius,
    _assess_observability,
    _assess_rollback,
    _recommend_deployment_strategy,
)
from change_review_orchestrator.agents.test_strategy import TestStrategyAgent
from change_review_orchestrator.domain.enums import (
    AgentStatus,
    AssetCategory,
    ChangeType,
    DataClassification,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState
from tests.fixtures.sample_diffs import (
    make_auth_refactor_case,
    make_docs_only_case,
    make_pci_tokenisation_case,
)


# ── _assess_rollback() tests ──────────────────────────────────────────────────

class TestAssessRollback:

    def _tiers(self, critical=0, high=0, medium=0, low=0):
        return {"critical": critical, "high": high, "medium": medium, "low": low}

    def test_no_risk_factors_viable(self) -> None:
        case = {"has_breaking_changes": False, "change_type": "FEATURE", "changed_files": []}
        ra = _assess_rollback(case, {"SOURCE_CODE"}, self._tiers())
        assert ra.viable is True
        assert len(ra.blockers) == 0

    def test_db_migration_adds_blocker(self) -> None:
        case = {"has_breaking_changes": False, "change_type": "FEATURE", "changed_files": []}
        ra = _assess_rollback(case, {"DATABASE_MIGRATION"}, self._tiers())
        assert len(ra.blockers) >= 1
        assert ra.rollback_score < 100

    def test_breaking_change_adds_blocker(self) -> None:
        case = {"has_breaking_changes": True, "change_type": "FEATURE", "changed_files": []}
        ra = _assess_rollback(case, {"SOURCE_CODE"}, self._tiers())
        assert len(ra.blockers) >= 1

    def test_iac_adds_warning_not_blocker(self) -> None:
        case = {"has_breaking_changes": False, "change_type": "FEATURE", "changed_files": []}
        ra = _assess_rollback(case, {"INFRASTRUCTURE_AS_CODE"}, self._tiers())
        assert len(ra.warnings) >= 1
        assert len(ra.blockers) == 0

    def test_hotfix_adds_warning(self) -> None:
        case = {"has_breaking_changes": False, "change_type": "HOTFIX", "changed_files": []}
        ra = _assess_rollback(case, {"SOURCE_CODE"}, self._tiers())
        assert any("hotfix" in w.lower() or "security" in w.lower() for w in ra.warnings)

    def test_score_decreases_with_each_risk(self) -> None:
        base = {"has_breaking_changes": False, "change_type": "FEATURE", "changed_files": []}
        risky = {"has_breaking_changes": True, "change_type": "HOTFIX", "changed_files": []}
        ra_base  = _assess_rollback(base,  {"SOURCE_CODE"}, self._tiers())
        ra_risky = _assess_rollback(risky, {"DATABASE_MIGRATION", "INFRASTRUCTURE_AS_CODE"},
                                    self._tiers(critical=3))
        assert ra_risky.rollback_score < ra_base.rollback_score

    def test_score_floored_at_zero(self) -> None:
        case = {"has_breaking_changes": True, "change_type": "HOTFIX", "changed_files": []}
        ra = _assess_rollback(
            case,
            {"DATABASE_MIGRATION", "INFRASTRUCTURE_AS_CODE"},
            {"critical": 5, "high": 3, "medium": 0, "low": 0},
        )
        assert ra.rollback_score >= 0


# ── _assess_observability() tests ─────────────────────────────────────────────

class TestAssessObservability:

    def test_full_labels_scores_high(self) -> None:
        case = {
            "labels": ["metrics", "alerting", "tracing", "logging", "health", "runbook"],
            "description": "",
        }
        oa = _assess_observability(case, set(), set())
        assert oa.score >= 80

    def test_no_labels_scores_zero(self) -> None:
        case = {"labels": [], "description": ""}
        oa = _assess_observability(case, set(), set())
        assert oa.score == 0

    def test_description_keywords_counted(self) -> None:
        case = {
            "labels": [],
            "description": "Added prometheus metrics and structured logging with structlog.",
        }
        oa = _assess_observability(case, set(), set())
        assert oa.score > 0
        assert "metrics/monitoring" in oa.present_signals

    def test_payment_concern_adds_missing_signal(self) -> None:
        case = {"labels": [], "description": ""}
        oa = _assess_observability(case, {"payment/pci"}, set())
        # Low score + payment concern should add a missing signal mention
        assert any("payment" in s.lower() or "pci" in s.lower() for s in oa.missing_signals)

    def test_present_and_missing_are_disjoint(self) -> None:
        case = {"labels": ["metrics", "alerting"], "description": ""}
        oa = _assess_observability(case, set(), set())
        assert set(oa.present_signals).isdisjoint(set(oa.missing_signals))


# ── _assess_blast_radius() tests ──────────────────────────────────────────────

class TestAssessBlastRadius:

    def _tiers(self, critical=0, high=0, medium=0, low=0):
        return {"critical": critical, "high": high, "medium": medium, "low": low}

    def test_no_risk_factors_low_blast(self) -> None:
        case = {"changed_files": []}
        br = _assess_blast_radius(case, {"SOURCE_CODE"}, self._tiers(low=2), set())
        assert br.estimated_consumers == "low"

    def test_breaking_change_increases_score(self) -> None:
        case = {"changed_files": [{"is_breaking_change": True}]}
        br = _assess_blast_radius(case, {"API_SCHEMA"}, self._tiers(critical=1), set())
        assert br.score >= 25

    def test_iac_adds_to_score(self) -> None:
        case = {"changed_files": []}
        br_no_iac = _assess_blast_radius(case, {"SOURCE_CODE"}, self._tiers(), set())
        br_iac    = _assess_blast_radius(case, {"INFRASTRUCTURE_AS_CODE"}, self._tiers(), set())
        assert br_iac.score > br_no_iac.score

    def test_score_capped_at_100(self) -> None:
        case = {"changed_files": [{"is_breaking_change": True}] * 5}
        br = _assess_blast_radius(
            case,
            {"INFRASTRUCTURE_AS_CODE", "DATABASE_MIGRATION", "API_SCHEMA"},
            {"critical": 5, "high": 5, "medium": 0, "low": 0},
            {"payment/pci"},
        )
        assert br.score <= 100


# ── _recommend_deployment_strategy() tests ────────────────────────────────────

class TestRecommendDeploymentStrategy:

    def _blast(self, score, consumers="low", breaking=0, cats=None):
        from change_review_orchestrator.agents.reliability import BlastRadius
        return BlastRadius(
            affected_categories=cats or ["SOURCE_CODE"],
            breaking_change_count=breaking,
            estimated_consumers=consumers,
            score=score,
        )

    def _rollback(self, viable=True, score=80):
        from change_review_orchestrator.agents.reliability import RollbackAssessment
        return RollbackAssessment(viable=viable, blockers=[], warnings=[], rollback_score=score)

    def _obs(self, score=80):
        from change_review_orchestrator.agents.reliability import ObservabilityAssessment
        return ObservabilityAssessment(score=score, missing_signals=[], present_signals=[])

    def test_hotfix_recommends_direct_with_monitoring(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(10), self._rollback(), self._obs(), "HOTFIX"
        )
        assert strategy == "direct-with-monitoring"

    def test_high_blast_recommends_blue_green(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(65), self._rollback(), self._obs(), "FEATURE"
        )
        assert strategy == "blue-green"

    def test_non_viable_rollback_recommends_blue_green(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(15), self._rollback(viable=False, score=20), self._obs(), "FEATURE"
        )
        assert strategy == "blue-green"

    def test_medium_blast_recommends_canary(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(35), self._rollback(), self._obs(), "FEATURE"
        )
        assert strategy == "canary"

    def test_low_blast_recommends_standard(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(5), self._rollback(), self._obs(), "DOCUMENTATION"
        )
        assert strategy == "standard"

    def test_db_migration_recommends_staged(self) -> None:
        strategy, _ = _recommend_deployment_strategy(
            self._blast(10, cats=["DATABASE_MIGRATION"]),
            self._rollback(),
            self._obs(),
            "DATABASE_MIGRATION",
        )
        assert strategy == "staged-migration"


# ── ReliabilityAgent end-to-end tests ────────────────────────────────────────

class TestReliabilityAgent:

    def _run_pipeline(self, case: ChangeCase) -> WorkflowState:
        state = WorkflowState(case=case)
        state = IntakeAgent().run(state)
        state = ImpactAgent().run(state)
        state = TestStrategyAgent().run(state)
        state = ReliabilityAgent().run(state)
        return state

    def test_agent_completes(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        assert state.agent_results["reliability"].status == AgentStatus.COMPLETED

    def test_all_required_metadata_keys_present(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["reliability"].metadata
        for key in ("deployment_risk_score", "rollback_score", "rollback_viable",
                    "observability_score", "blast_radius_score", "deployment_strategy"):
            assert key in meta, f"Missing metadata key: {key}"

    def test_deployment_risk_score_is_0_to_100(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        score = state.agent_results["reliability"].metadata["deployment_risk_score"]
        assert 0 <= score <= 100

    def test_rollback_blocker_finding_for_db_migration(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        findings = state.agent_results["reliability"].findings
        blocker_findings = [f for f in findings if "blocker" in f.title.lower()]
        assert len(blocker_findings) >= 1

    def test_all_findings_are_reliability_category(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        for f in state.agent_results["reliability"].findings:
            assert f.category == FindingCategory.RELIABILITY

    def test_docs_only_has_minimal_findings(self) -> None:
        state = self._run_pipeline(make_docs_only_case())
        findings = state.agent_results["reliability"].findings
        critical_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        assert len(critical_high) == 0

    def test_strategy_in_metadata(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        strategy = state.agent_results["reliability"].metadata["deployment_strategy"]
        assert strategy in ("standard", "canary", "blue-green",
                            "staged-migration", "direct-with-monitoring")

    def test_evidence_item_created(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        labels = [e.label for e in state.agent_results["reliability"].evidence_items]
        assert "Reliability Assessment" in labels

    def test_summary_includes_strategy(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        summary = state.agent_results["reliability"].summary
        assert "strategy" in summary.lower()

    def test_findings_merged_to_all_findings(self) -> None:
        state = self._run_pipeline(make_pci_tokenisation_case())
        rel_count = len(state.agent_results["reliability"].findings)
        assert rel_count <= len(state.all_findings)
