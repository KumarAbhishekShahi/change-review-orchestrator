"""
Reliability Agent — Change Review Orchestrator

Responsibilities:
1. Assess deployment risk based on change type, tier distribution, and churn
2. Evaluate rollback viability (DB migrations, IaC, breaking API changes)
3. Score observability readiness (metrics, logging, alerting indicators)
4. Compute blast radius — how many systems/services are affected
5. Recommend deployment strategy: direct, canary, feature-flag, or blue-green
6. Raise findings for missing rollback plans, high blast radius, low observability

Design: deterministic rule-based assessment using metadata from prior agents.
No LLM required for this step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import (
    AssetCategory,
    ChangeType,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import (
    AgentResult,
    EvidenceItem,
    Finding,
    WorkflowState,
)

logger = structlog.get_logger(__name__)


# ── Deployment risk weights ────────────────────────────────────────────────
# Contributes to a 0-100 deployment risk score (higher = riskier).

_CHANGE_TYPE_RISK: dict[ChangeType, int] = {
    ChangeType.HOTFIX:          40,
    ChangeType.SECURITY_PATCH:  35,
    ChangeType.DATABASE_MIGRATION: 30,
    ChangeType.FEATURE:         20,
    ChangeType.REFACTOR:        25,
    ChangeType.BUG_FIX:         15,
    ChangeType.CONFIGURATION:   20,
    ChangeType.DEPENDENCY_UPGRADE: 15,
    ChangeType.DOCUMENTATION:   2,
    ChangeType.UNKNOWN:           15,
}

_TIER_RISK: dict[str, int] = {
    "critical": 30,
    "high":     20,
    "medium":   8,
    "low":      2,
}


@dataclass
class RollbackAssessment:
    viable: bool
    blockers: list[str]
    warnings: list[str]
    rollback_score: int   # 0 = no rollback possible, 100 = trivial rollback


@dataclass
class ObservabilityAssessment:
    score: int            # 0-100
    missing_signals: list[str]
    present_signals: list[str]


@dataclass
class BlastRadius:
    affected_categories: list[str]
    breaking_change_count: int
    estimated_consumers: str   # "low" | "medium" | "high" | "critical"
    score: int                 # 0-100


def _assess_rollback(
    case_dict: dict[str, Any],
    asset_categories: set[str],
    tier_counts: dict[str, int],
) -> RollbackAssessment:
    """
    Evaluate rollback viability for the changeset.

    Key risk factors:
    - DB migrations without a reversal path are hard to roll back
    - IaC changes that delete resources are irreversible
    - Breaking API changes require coordinated consumer rollback
    - Hotfix/security patch changesets are typically urgent and skip normal review
    """
    blockers: list[str] = []
    warnings: list[str] = []
    rollback_score = 100

    has_db_migration   = "DATABASE_MIGRATION" in asset_categories
    has_iac            = "INFRASTRUCTURE_AS_CODE" in asset_categories
    has_breaking       = case_dict.get("has_breaking_changes", False)
    change_type_value  = case_dict.get("change_type", "OTHER")
    is_hotfix          = change_type_value in ("HOTFIX", "SECURITY_PATCH")
    critical_count     = tier_counts.get("critical", 0)

    if has_db_migration:
        blockers.append(
            "Database migration present — rollback requires a verified reversal migration "
            "and data-loss risk assessment."
        )
        rollback_score -= 30

    if has_iac:
        warnings.append(
            "IaC changes may not be trivially reversible if infrastructure was destroyed "
            "or recreated. Confirm terraform state is tracked."
        )
        rollback_score -= 15

    if has_breaking:
        blockers.append(
            "Breaking API changes: rolling back after consumers have migrated may cause "
            "downstream failures. Coordinate rollback window with consumers."
        )
        rollback_score -= 25

    if is_hotfix:
        warnings.append(
            "Hotfix/security patch: expedited deployment increases rollback complexity "
            "if the fix introduces a regression."
        )
        rollback_score -= 10

    if critical_count >= 2:
        warnings.append(
            f"{critical_count} critical-tier files — high coupling risk increases the chance "
            "that a partial rollback leaves the system in an inconsistent state."
        )
        rollback_score -= 10

    rollback_score = max(0, min(100, rollback_score))
    viable = len(blockers) == 0 and rollback_score >= 40

    return RollbackAssessment(
        viable=viable,
        blockers=blockers,
        warnings=warnings,
        rollback_score=rollback_score,
    )


def _assess_observability(
    case_dict: dict[str, Any],
    concerns: set[str],
    asset_categories: set[str],
) -> ObservabilityAssessment:
    """
    Score observability readiness based on PR metadata signals.

    Signals checked (heuristic — inferred from labels, file paths, descriptions):
    - Metrics: labels contain 'metrics' or 'monitoring'
    - Logging: 'logging' or 'structured-log' in labels/description
    - Alerting: 'alerting' or 'alert' in labels
    - Tracing: 'tracing' or 'otel' in labels
    - Health check: 'health' in changed files or labels
    - Rollback runbook: 'runbook' or 'rollback' in PR description
    """
    labels: list[str] = [lbl.lower() for lbl in (case_dict.get("labels") or [])]
    description: str = (case_dict.get("description") or "").lower()
    text = " ".join(labels) + " " + description

    signal_checks = [
        ("metrics/monitoring",   any(kw in text for kw in ("metrics", "monitoring", "prometheus", "datadog"))),
        ("structured logging",   any(kw in text for kw in ("logging", "structured-log", "structlog", "log"))),
        ("alerting",             any(kw in text for kw in ("alert", "pagerduty", "opsgenie"))),
        ("distributed tracing",  any(kw in text for kw in ("tracing", "otel", "opentelemetry", "jaeger", "trace"))),
        ("health check",         any(kw in text for kw in ("health", "liveness", "readiness"))),
        ("rollback runbook",     any(kw in text for kw in ("runbook", "rollback plan", "recovery"))),
    ]

    present: list[str] = [name for name, found in signal_checks if found]
    missing: list[str] = [name for name, found in signal_checks if not found]

    # Score: each present signal is worth ~16 points (6 signals → max 96; round to 100)
    score = min(100, len(present) * 17)

    # High-risk concerns demand higher observability
    if "payment/pci" in concerns and score < 50:
        missing.append("payment observability baseline (PCI requires audit logging)")

    if "auth/authz" in concerns and "structured logging" not in present:
        missing.append("auth event logging (required for security forensics)")

    return ObservabilityAssessment(
        score=score,
        missing_signals=missing,
        present_signals=present,
    )


def _assess_blast_radius(
    case_dict: dict[str, Any],
    asset_categories: set[str],
    tier_counts: dict[str, int],
    concerns: set[str],
) -> BlastRadius:
    """
    Estimate the blast radius — how many systems/services the change may affect.
    """
    breaking_count = sum(
        1 for f in case_dict.get("changed_files", [])
        if f.get("is_breaking_change")
    )
    critical_count = tier_counts.get("critical", 0)
    high_count = tier_counts.get("high", 0)

    # Score: weighted sum
    score = (
        breaking_count * 25
        + critical_count * 15
        + high_count * 8
        + (20 if "INFRASTRUCTURE_AS_CODE" in asset_categories else 0)
        + (15 if "DATABASE_MIGRATION" in asset_categories else 0)
        + (10 if "API_SCHEMA" in asset_categories else 0)
        + (10 if "payment/pci" in concerns else 0)
    )
    score = min(100, score)

    # Consumer estimate
    if score >= 70:
        consumers = "critical"
    elif score >= 45:
        consumers = "high"
    elif score >= 20:
        consumers = "medium"
    else:
        consumers = "low"

    return BlastRadius(
        affected_categories=sorted(asset_categories),
        breaking_change_count=breaking_count,
        estimated_consumers=consumers,
        score=score,
    )


def _recommend_deployment_strategy(
    blast: BlastRadius,
    rollback: RollbackAssessment,
    observability: ObservabilityAssessment,
    change_type: str,
) -> tuple[str, str]:
    """
    Recommend a deployment strategy and provide rationale.

    Returns:
        (strategy_name, rationale)
    """
    if change_type in ("HOTFIX", "SECURITY_PATCH"):
        return (
            "direct-with-monitoring",
            "Hotfix/security patch requires fast deployment. Deploy directly but with "
            "enhanced monitoring and an on-call engineer on standby for immediate rollback.",
        )

    if blast.score >= 60 or not rollback.viable:
        return (
            "blue-green",
            "High blast radius or non-viable rollback — blue-green deployment allows "
            "instant traffic switch-back without a code rollback.",
        )

    if blast.score >= 30 or observability.score < 50:
        return (
            "canary",
            "Medium blast radius or low observability score — canary deployment limits "
            "exposure to a small traffic percentage while metrics are observed.",
        )

    if "DATABASE_MIGRATION" in blast.affected_categories:
        return (
            "staged-migration",
            "DB migration present — deploy application first, run migration, verify, "
            "then route traffic. Keep rollback migration script ready.",
        )

    return (
        "standard",
        "Low blast radius, viable rollback, and adequate observability — "
        "standard deployment with normal change-window controls.",
    )


class ReliabilityAgent(BaseAgent):
    """
    Reliability Agent.

    Produces a holistic deployment-readiness assessment covering rollback
    viability, observability readiness, blast radius, and deployment strategy
    recommendation. Raises findings for every reliability gap that could lead
    to an incident.
    """

    agent_name = "reliability"

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("reliability_assessment_started")

        case_dict = case.model_dump()

        # Collect context from prior agents
        impact_result = state.agent_results.get("impact")
        ts_result = state.agent_results.get("test_strategy")

        tier_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        concerns: set[str] = set()

        if impact_result:
            tier_counts = impact_result.metadata.get("tier_counts", tier_counts)
            concerns = set(impact_result.metadata.get("all_concerns", []))

        overall_confidence: int = 0
        if ts_result:
            overall_confidence = ts_result.metadata.get("overall_confidence_score", 0)

        asset_categories: set[str] = {cf.category.value for cf in case.changed_files}

        # ── Compute risk score ────────────────────────────────────────────────
        change_type_risk = _CHANGE_TYPE_RISK.get(case.change_type, 15)
        tier_risk = sum(
            _TIER_RISK.get(tier, 0) * count
            for tier, count in tier_counts.items()
        )
        deployment_risk_score = min(100, change_type_risk + tier_risk)

        # ── Sub-assessments ───────────────────────────────────────────────────
        rollback  = _assess_rollback(case_dict, asset_categories, tier_counts)
        observability = _assess_observability(case_dict, concerns, asset_categories)
        blast = _assess_blast_radius(case_dict, asset_categories, tier_counts, concerns)
        strategy, strategy_rationale = _recommend_deployment_strategy(
            blast, rollback, observability, case.change_type.value
        )

        log.info(
            "reliability_scores",
            deployment_risk=deployment_risk_score,
            rollback_score=rollback.rollback_score,
            observability_score=observability.score,
            blast_score=blast.score,
            strategy=strategy,
        )

        # ── Raise findings ────────────────────────────────────────────────────

        # 1. Rollback blockers
        for blocker in rollback.blockers:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.HIGH,
                title="Rollback blocker identified",
                description=blocker,
                remediation_guidance=(
                    "Resolve this blocker before approving the deployment. "
                    "Attach evidence (rollback script, consumer coordination plan) to the PR."
                ),
            ))

        # 2. Rollback warnings
        for warning in rollback.warnings:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.MEDIUM,
                title="Rollback complexity warning",
                description=warning,
                remediation_guidance=(
                    "Document the rollback procedure in the PR description or runbook."
                ),
            ))

        # 3. Observability gaps
        if observability.score < 50:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.HIGH,
                title="Low observability readiness",
                description=(
                    f"Observability score: {observability.score}/100. "
                    f"Missing signals: {', '.join(observability.missing_signals)}. "
                    "Deploying without adequate observability increases MTTD for incidents."
                ),
                remediation_guidance=(
                    "Add metrics instrumentation, structured logging, and alerting "
                    "before deploying. Reference the Observability Runbook in the PR."
                ),
            ))
        elif observability.score < 75:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.MEDIUM,
                title="Partial observability coverage",
                description=(
                    f"Observability score: {observability.score}/100. "
                    f"Missing: {', '.join(observability.missing_signals[:3])}."
                ),
                remediation_guidance=(
                    "Consider adding the missing observability signals before go-live."
                ),
            ))

        # 4. Blast radius
        if blast.score >= 70:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.CRITICAL,
                title="Critical blast radius — wide-impact deployment",
                description=(
                    f"Blast radius score: {blast.score}/100. "
                    f"Estimated consumer impact: {blast.estimated_consumers}. "
                    f"Breaking changes: {blast.breaking_change_count}. "
                    f"Affected categories: {', '.join(blast.affected_categories)}."
                ),
                remediation_guidance=(
                    "Use blue-green or feature-flag deployment. Coordinate with all "
                    "downstream consumers before merging. Schedule a deployment window."
                ),
            ))
        elif blast.score >= 40:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.HIGH,
                title="High blast radius — canary deployment recommended",
                description=(
                    f"Blast radius score: {blast.score}/100. "
                    f"Consumer impact: {blast.estimated_consumers}."
                ),
                remediation_guidance=(
                    "Use canary deployment. Monitor error rates and latency at 1%, 5%, "
                    "25%, 100% traffic before full rollout."
                ),
            ))

        # 5. Low test confidence → deployment risk
        if overall_confidence < 50:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.RELIABILITY,
                severity=Severity.HIGH,
                title="Low test confidence increases deployment risk",
                description=(
                    f"Overall test confidence: {overall_confidence}/100. "
                    "Deploying changes with insufficient test coverage significantly "
                    "increases the probability of a production incident."
                ),
                remediation_guidance=(
                    "Address all HIGH-severity test strategy gaps before approving deployment."
                ),
            ))

        # ── Metadata ──────────────────────────────────────────────────────────
        result.metadata.update({
            "deployment_risk_score":  deployment_risk_score,
            "rollback_score":         rollback.rollback_score,
            "rollback_viable":        rollback.viable,
            "rollback_blockers":      rollback.blockers,
            "observability_score":    observability.score,
            "observability_missing":  observability.missing_signals,
            "blast_radius_score":     blast.score,
            "blast_consumers":        blast.estimated_consumers,
            "deployment_strategy":    strategy,
            "strategy_rationale":     strategy_rationale,
        })

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Reliability Assessment",
            content_summary=(
                f"Deployment risk: {deployment_risk_score}/100. "
                f"Rollback: {'viable' if rollback.viable else 'BLOCKED'} "
                f"(score {rollback.rollback_score}/100). "
                f"Observability: {observability.score}/100. "
                f"Blast radius: {blast.score}/100 ({blast.estimated_consumers}). "
                f"Strategy: {strategy}."
            ),
        ))

        result.summary = (
            f"Reliability assessment complete. "
            f"Deployment risk: {deployment_risk_score}/100. "
            f"Rollback: {'viable' if rollback.viable else 'BLOCKED'}. "
            f"Observability: {observability.score}/100. "
            f"Blast radius: {blast.estimated_consumers}. "
            f"Recommended strategy: {strategy}."
        )

        log.info(
            "reliability_assessment_complete",
            findings=len(result.findings),
            strategy=strategy,
        )
        return result
