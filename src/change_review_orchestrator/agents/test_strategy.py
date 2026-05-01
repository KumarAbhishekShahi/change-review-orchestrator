"""
Test Strategy Agent — Change Review Orchestrator

Responsibilities:
1. Map each changed file to its test obligations (unit, integration, contract,
   performance, security, regression) based on asset category and concern labels
2. Check whether a corresponding test file exists in the changeset
3. Detect coverage gaps — missing test types for high-risk files
4. Score test confidence 0-100 per component and overall
5. Recommend additional test types required before merge
6. Produce structured findings for every material gap

Design: deterministic rule-based mapping. No LLM required.
Test file presence is inferred from the PR changeset itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import (
    AssetCategory,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import (
    AgentResult,
    ChangedFile,
    EvidenceItem,
    Finding,
    WorkflowState,
)

logger = structlog.get_logger(__name__)


# ── Test obligation matrix ─────────────────────────────────────────────────
# Maps AssetCategory → set of required test types for a "green" test strategy.
# Each entry: (required_types, optional_types)

@dataclass(frozen=True)
class TestObligation:
    required: frozenset[str]
    optional: frozenset[str] = field(default_factory=frozenset)


_CATEGORY_OBLIGATIONS: dict[AssetCategory, TestObligation] = {
    AssetCategory.SOURCE_CODE: TestObligation(
        required=frozenset({"unit"}),
        optional=frozenset({"integration", "regression"}),
    ),
    AssetCategory.API_SCHEMA: TestObligation(
        required=frozenset({"contract", "integration"}),
        optional=frozenset({"performance", "regression"}),
    ),
    AssetCategory.DATABASE_MIGRATION: TestObligation(
        required=frozenset({"migration_rollback", "integration"}),
        optional=frozenset({"performance"}),
    ),
    AssetCategory.INFRASTRUCTURE_AS_CODE: TestObligation(
        required=frozenset({"plan_dry_run", "integration"}),
        optional=frozenset({"security_scan"}),
    ),
    AssetCategory.DEPENDENCY_MANIFEST: TestObligation(
        required=frozenset({"sca_scan"}),
        optional=frozenset({"regression"}),
    ),
    AssetCategory.CI_CD_PIPELINE: TestObligation(
        required=frozenset({"pipeline_validation"}),
        optional=frozenset({"security_scan"}),
    ),
    AssetCategory.CONFIGURATION: TestObligation(
        required=frozenset({"integration"}),
        optional=frozenset({"regression"}),
    ),
    AssetCategory.TEST: TestObligation(
        required=frozenset(),   # test files don't need their own tests
        optional=frozenset(),
    ),
    AssetCategory.DOCUMENTATION: TestObligation(
        required=frozenset(),
        optional=frozenset(),
    ),
    AssetCategory.SECRET_OR_CREDENTIAL: TestObligation(
        required=frozenset({"security_scan"}),
        optional=frozenset(),
    ),
    AssetCategory.UNKNOWN: TestObligation(
        required=frozenset({"unit"}),
        optional=frozenset(),
    ),
}

# Extra obligation injected by concern labels (from ImpactAgent)
_CONCERN_EXTRA_OBLIGATIONS: dict[str, frozenset[str]] = {
    "payment/pci":         frozenset({"security_scan", "integration", "regression"}),
    "auth/authz":          frozenset({"security_scan", "integration"}),
    "crypto/tls":          frozenset({"security_scan"}),
    "interface/api/schema": frozenset({"contract"}),
    "audit/compliance":    frozenset({"integration"}),
    "breaking-change":     frozenset({"contract", "regression"}),
}

# Test type descriptions (used in finding text)
_TEST_TYPE_LABELS: dict[str, str] = {
    "unit":               "Unit tests",
    "integration":        "Integration tests",
    "contract":           "Contract / API compatibility tests",
    "regression":         "Regression test suite",
    "performance":        "Performance / load tests",
    "security_scan":      "Security scan (SAST/DAST/SCA)",
    "migration_rollback": "DB migration rollback test",
    "plan_dry_run":       "IaC plan / dry-run (terraform plan or equivalent)",
    "pipeline_validation": "CI/CD pipeline syntax and logic validation",
    "sca_scan":           "Software Composition Analysis (SCA) scan",
}

# Confidence deductions per missing required test type
_CONFIDENCE_DEDUCTIONS: dict[str, int] = {
    "unit":               25,
    "integration":        20,
    "contract":           20,
    "regression":         10,
    "performance":        5,
    "security_scan":      15,
    "migration_rollback": 25,
    "plan_dry_run":       20,
    "pipeline_validation": 15,
    "sca_scan":           15,
}


@dataclass
class ComponentTestResult:
    path: str
    category: AssetCategory
    required_types: frozenset[str]
    present_types: frozenset[str]
    missing_types: frozenset[str]
    confidence_score: int      # 0-100
    has_test_file_in_pr: bool


def _detect_test_presence(
    changed_files: list[ChangedFile],
    source_path: str,
) -> bool:
    """
    Heuristically detect whether a test file for source_path is in the PR.

    Matching strategy:
    - Look for files in tests/ or test_/ directories
    - Look for files named test_<module> or <module>_test
    - Strip path prefix and compare stem names
    """
    import re as _re
    import os

    stem = os.path.splitext(os.path.basename(source_path))[0]
    test_patterns = [
        _re.compile(rf"test[_/]{_re.escape(stem)}", _re.I),
        _re.compile(rf"{_re.escape(stem)}[_/]test", _re.I),
        _re.compile(rf"tests?/.*{_re.escape(stem)}", _re.I),
    ]

    for cf in changed_files:
        if cf.category == AssetCategory.TEST:
            for pat in test_patterns:
                if pat.search(cf.path):
                    return True
    return False


def _compute_confidence(
    required: frozenset[str],
    missing: frozenset[str],
    has_test_file: bool,
) -> int:
    """
    Compute a 0-100 confidence score for test coverage of a component.

    Starts at 100, deducts for each missing required test type,
    and adds a small bonus if a test file was found in the PR.
    """
    score = 100
    for t in missing:
        score -= _CONFIDENCE_DEDUCTIONS.get(t, 10)
    if not has_test_file and required:
        score -= 10    # extra deduction if no test file present
    return max(0, min(100, score))


class TestStrategyAgent(BaseAgent):
    """
    Test Strategy Agent.

    Maps each changed file to its test obligations, checks the PR changeset
    for test file presence, identifies gaps, and scores confidence per
    component and overall.
    """

    agent_name = "test_strategy"

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("test_strategy_started", files=len(case.changed_files))

        # Collect impact concerns for extra obligation injection
        impact_result = state.agent_results.get("impact")
        concerns: set[str] = set()
        if impact_result and "all_concerns" in impact_result.metadata:
            concerns = set(impact_result.metadata["all_concerns"])

        # ── Map each file to obligations ──────────────────────────────────────
        component_results: list[ComponentTestResult] = []
        gap_findings: list[Finding] = []
        total_confidence = 0

        for cf in case.changed_files:
            obligation = _CATEGORY_OBLIGATIONS.get(
                cf.category, _CATEGORY_OBLIGATIONS[AssetCategory.UNKNOWN]
            )

            # Inject concern-based extra obligations
            extra: set[str] = set()
            for concern, extra_types in _CONCERN_EXTRA_OBLIGATIONS.items():
                if concern in concerns:
                    extra.update(extra_types)

            required = obligation.required | frozenset(extra)

            # Check PR changeset for test file presence
            has_test_file = _detect_test_presence(case.changed_files, cf.path)

            # Determine which test types are "present":
            # - TEST category files count as unit coverage for SOURCE_CODE
            # - SCA/security scan types assumed absent unless labelled
            present: set[str] = set()
            if has_test_file:
                present.add("unit")

            # IaC plan presence inferred from dry-run label on PR
            if cf.category == AssetCategory.INFRASTRUCTURE_AS_CODE:
                labels = case.labels or []
                if any("plan" in lbl.lower() or "dry-run" in lbl.lower() for lbl in labels):
                    present.add("plan_dry_run")

            # SCA scan inferred from PR labels
            if cf.category == AssetCategory.DEPENDENCY_MANIFEST:
                labels = case.labels or []
                if any("sca" in lbl.lower() or "scan" in lbl.lower() for lbl in labels):
                    present.add("sca_scan")

            present_frozen = frozenset(present)
            missing = required - present_frozen
            confidence = _compute_confidence(required, missing, has_test_file)

            ctr = ComponentTestResult(
                path=cf.path,
                category=cf.category,
                required_types=required,
                present_types=present_frozen,
                missing_types=missing,
                confidence_score=confidence,
                has_test_file_in_pr=has_test_file,
            )
            component_results.append(ctr)
            total_confidence += confidence

            log.debug(
                "component_mapped",
                path=cf.path,
                required=sorted(required),
                missing=sorted(missing),
                confidence=confidence,
            )

            # ── Raise finding for material gaps ──────────────────────────────
            if missing and cf.category not in (AssetCategory.TEST, AssetCategory.DOCUMENTATION):
                # Severity based on criticality of what's missing
                if any(t in missing for t in ("unit", "migration_rollback", "contract")):
                    sev = Severity.HIGH
                elif any(t in missing for t in ("security_scan", "integration", "plan_dry_run")):
                    sev = Severity.MEDIUM
                else:
                    sev = Severity.LOW

                missing_labels = [_TEST_TYPE_LABELS.get(t, t) for t in sorted(missing)]
                gap_findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.TEST_STRATEGY,
                    severity=sev,
                    title=f"Test coverage gap: {cf.path}",
                    description=(
                        f"File '{cf.path}' ({cf.category.value}) is missing required test types: "
                        f"{', '.join(missing_labels)}. "
                        f"Test confidence score: {confidence}/100."
                    ),
                    affected_assets=[cf.path],
                    remediation_guidance=(
                        f"Add the following before merging: {', '.join(missing_labels)}."
                    ),
                ))

        result.findings.extend(gap_findings)

        # ── Overall confidence score ──────────────────────────────────────────
        file_count = len(case.changed_files)
        overall_confidence = (
            total_confidence // file_count if file_count else 100
        )

        # Aggregate missing types across all components
        all_missing: set[str] = set()
        for ctr in component_results:
            all_missing.update(ctr.missing_types)

        # ── Structured metadata ───────────────────────────────────────────────
        result.metadata["component_results"] = [
            {
                "path": ctr.path,
                "category": ctr.category.value,
                "required_types": sorted(ctr.required_types),
                "present_types": sorted(ctr.present_types),
                "missing_types": sorted(ctr.missing_types),
                "confidence_score": ctr.confidence_score,
                "has_test_file_in_pr": ctr.has_test_file_in_pr,
            }
            for ctr in component_results
        ]
        result.metadata["overall_confidence_score"] = overall_confidence
        result.metadata["all_missing_test_types"] = sorted(all_missing)
        result.metadata["gap_count"] = len(gap_findings)

        # ── Evidence item ─────────────────────────────────────────────────────
        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Test Strategy Matrix",
            content_summary=(
                f"{file_count} files analysed. "
                f"Overall test confidence: {overall_confidence}/100. "
                f"Coverage gaps: {len(gap_findings)}. "
                f"Missing test types: {', '.join(sorted(all_missing)) or 'none'}."
            ),
        ))

        result.summary = (
            f"Test strategy complete. Overall confidence: {overall_confidence}/100. "
            f"Gaps: {len(gap_findings)} across {file_count} files. "
            f"Missing types: {', '.join(sorted(all_missing)) or 'none'}."
        )

        log.info(
            "test_strategy_complete",
            overall_confidence=overall_confidence,
            gaps=len(gap_findings),
            missing_types=sorted(all_missing),
        )
        return result
