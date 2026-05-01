"""
Change Impact Agent — Change Review Orchestrator

Responsibilities:
1. Categorise changed files into impact tiers (critical / high / medium / low)
2. Detect interface, schema, DB, IaC, dependency, and pipeline changes
3. Flag breaking changes with structured reasons
4. Build a lightweight impact graph (component → affected concerns)
5. Compute an overall change-risk score
6. Produce structured findings for every high/critical impact category detected

Design principle: all heuristics are deterministic regex + rule-based.
No LLM calls required for this agent.
"""

from __future__ import annotations

import re
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


# ── Impact tier thresholds ─────────────────────────────────────────────────
# Files are scored 0-100; score drives the tier label.
_CHURN_HIGH_THRESHOLD = 300   # lines changed → high churn risk
_CHURN_CRITICAL_THRESHOLD = 800


# ── High-risk path patterns ────────────────────────────────────────────────
# Patterns that indicate files touching critical concerns regardless of size.

_INTERFACE_PATTERNS = re.compile(
    r"(api|interface|contract|client|gateway|facade|openapi|swagger|proto|graphql|schema)",
    re.I,
)
_AUTH_PATTERNS = re.compile(
    r"(auth|authn|authz|oauth|jwt|token|session|permission|role|acl|rbac|saml|sso)",
    re.I,
)
_CRYPTO_PATTERNS = re.compile(
    r"(crypto|cipher|encrypt|decrypt|hash|hmac|sign|verify|tls|ssl|cert|key)",
    re.I,
)
_PAYMENT_PATTERNS = re.compile(
    r"(payment|transaction|transfer|ledger|account|balance|settlement|clearing|pci)",
    re.I,
)
_AUDIT_LOG_PATTERNS = re.compile(
    r"(audit|compliance|log|trail|event.?store|journal)",
    re.I,
)


@dataclass
class ImpactNode:
    """
    A node in the impact graph representing one changed component.

    Attributes:
        path:           File path of the changed component.
        category:       Asset category of the file.
        concerns:       List of concern labels detected (e.g. ['auth', 'crypto']).
        risk_score:     Numeric risk score 0–100.
        impact_tier:    'critical' | 'high' | 'medium' | 'low'
        breaking:       True if this file carries a breaking-change flag.
    """
    path: str
    category: AssetCategory
    concerns: list[str] = field(default_factory=list)
    risk_score: int = 0
    impact_tier: str = "low"
    breaking: bool = False


def _score_file(cf: ChangedFile) -> tuple[int, list[str]]:
    """
    Compute a risk score and concern list for a single ChangedFile.

    Scoring components (additive, capped at 100):
    - Base: churn (lines added + removed) scaled logarithmically
    - +30 if file touches an interface/API/schema concern
    - +25 if file touches auth/authz/session concern
    - +25 if file touches crypto/TLS/cert concern
    - +20 if file touches payment/transaction/PCI concern
    - +15 if file touches audit/compliance/logging concern
    - +20 if is_breaking_change flag is set
    - +15 for DATABASE_MIGRATION or INFRASTRUCTURE_AS_CODE category
    - +10 for DEPENDENCY_MANIFEST (supply chain risk)
    - +10 for CI_CD_PIPELINE (pipeline integrity)

    Returns:
        (score, concerns) where score is 0-100 and concerns is a list of labels.
    """
    score = 0
    concerns: list[str] = []
    path = cf.path

    # Churn contribution (max 20 points)
    churn = cf.lines_added + cf.lines_removed
    if churn >= _CHURN_CRITICAL_THRESHOLD:
        score += 20
    elif churn >= _CHURN_HIGH_THRESHOLD:
        score += 12
    elif churn >= 100:
        score += 6
    elif churn >= 30:
        score += 3

    # Pattern-based concern detection
    if _INTERFACE_PATTERNS.search(path):
        score += 30
        concerns.append("interface/api/schema")
    if _AUTH_PATTERNS.search(path):
        score += 25
        concerns.append("auth/authz")
    if _CRYPTO_PATTERNS.search(path):
        score += 25
        concerns.append("crypto/tls")
    if _PAYMENT_PATTERNS.search(path):
        score += 20
        concerns.append("payment/pci")
    if _AUDIT_LOG_PATTERNS.search(path):
        score += 15
        concerns.append("audit/compliance")

    # Category-based risk
    category_bonus: dict[AssetCategory, int] = {
        AssetCategory.DATABASE_MIGRATION: 15,
        AssetCategory.INFRASTRUCTURE_AS_CODE: 15,
        AssetCategory.DEPENDENCY_MANIFEST: 10,
        AssetCategory.CI_CD_PIPELINE: 10,
        AssetCategory.API_SCHEMA: 20,
        AssetCategory.SECRET_OR_CREDENTIAL: 40,  # should never reach impact agent
    }
    bonus = category_bonus.get(cf.category, 0)
    if bonus:
        score += bonus
        if cf.category not in {AssetCategory.SOURCE_CODE, AssetCategory.TEST}:
            concerns.append(cf.category.value.lower().replace("_", "/"))

    # Breaking change penalty
    if cf.is_breaking_change:
        score += 20
        concerns.append("breaking-change")

    return min(score, 100), concerns


def _tier_from_score(score: int) -> str:
    """Map numeric risk score to a named tier."""
    if score >= 70:
        return "critical"
    if score >= 45:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


class ImpactAgent(BaseAgent):
    """
    Change Impact Agent.

    Analyses the changed files from the Intake agent's output and
    builds a structured impact graph with risk tiers and concern labels.
    Produces findings for every critical/high-tier component and for
    specific categories (DB migration, IaC, auth, crypto, etc.).
    """

    agent_name = "impact"

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        """
        Build the impact graph and raise findings for high-risk changes.
        """
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("impact_analysis_started", files=len(case.changed_files))

        # ── Build impact graph ────────────────────────────────────────────────
        impact_graph: list[dict[str, Any]] = []
        tier_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        all_concerns: set[str] = set()

        # Collect categorised files from intake metadata if available
        intake_meta = state.agent_results.get("intake", None)
        classified_map: dict[str, str] = {}
        if intake_meta and "classified_files" in intake_meta.metadata:
            classified_map = {
                cf["path"]: cf["category"]
                for cf in intake_meta.metadata["classified_files"]
            }

        for cf in case.changed_files:
            # Use intake-classified category if richer than the model default
            if classified_map.get(cf.path):
                try:
                    effective_category = AssetCategory(classified_map[cf.path])
                except ValueError:
                    effective_category = cf.category
            else:
                effective_category = cf.category

            # Rebuild a copy with the effective category for scoring
            scored_cf = cf.model_copy(update={"category": effective_category})
            score, concerns = _score_file(scored_cf)
            tier = _tier_from_score(score)
            tier_counts[tier] += 1
            all_concerns.update(concerns)

            node = ImpactNode(
                path=cf.path,
                category=effective_category,
                concerns=concerns,
                risk_score=score,
                impact_tier=tier,
                breaking=cf.is_breaking_change,
            )
            impact_graph.append({
                "path": node.path,
                "category": node.category.value,
                "concerns": node.concerns,
                "risk_score": node.risk_score,
                "impact_tier": node.impact_tier,
                "breaking": node.breaking,
                "churn": cf.churn,
            })

            log.debug(
                "file_scored",
                path=cf.path,
                score=score,
                tier=tier,
                concerns=concerns,
            )

        result.metadata["impact_graph"] = impact_graph
        result.metadata["tier_counts"] = tier_counts
        result.metadata["all_concerns"] = sorted(all_concerns)
        log.info(
            "impact_graph_built",
            nodes=len(impact_graph),
            tiers=tier_counts,
            concerns=sorted(all_concerns),
        )

        # ── Raise findings for high-risk nodes ───────────────────────────────
        for node_data in impact_graph:
            tier = node_data["impact_tier"]
            path = node_data["path"]
            concerns = node_data["concerns"]
            score = node_data["risk_score"]

            if tier == "critical":
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.IMPACT,
                    severity=Severity.CRITICAL,
                    title=f"Critical-tier change: {path}",
                    description=(
                        f"File '{path}' scored {score}/100 (critical tier). "
                        f"Detected concerns: {', '.join(concerns) or 'high churn'}. "
                        "Requires thorough review before merge."
                    ),
                    affected_assets=[path],
                    remediation_guidance=(
                        "Ensure this file has paired test coverage, a reviewer with domain "
                        "expertise, and an explicit sign-off in the PR."
                    ),
                ))

            elif tier == "high":
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.IMPACT,
                    severity=Severity.HIGH,
                    title=f"High-tier change: {path}",
                    description=(
                        f"File '{path}' scored {score}/100 (high tier). "
                        f"Concerns: {', '.join(concerns) or 'elevated churn'}."
                    ),
                    affected_assets=[path],
                ))

        # ── Category-specific structural findings ─────────────────────────────
        self._raise_category_findings(case.changed_files, result, log)

        # ── Overall change-risk summary ───────────────────────────────────────
        critical_count = tier_counts["critical"]
        high_count = tier_counts["high"]
        overall_severity = (
            Severity.CRITICAL if critical_count >= 2
            else Severity.HIGH if critical_count >= 1 or high_count >= 3
            else Severity.MEDIUM if high_count >= 1
            else Severity.LOW
        )

        result.metadata["overall_severity"] = overall_severity.value
        result.metadata["overall_risk_score"] = (
            sum(n["risk_score"] for n in impact_graph) // max(len(impact_graph), 1)
        )

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Impact Graph",
            content_summary=(
                f"{len(impact_graph)} files analysed. "
                f"Tier breakdown — critical: {tier_counts['critical']}, "
                f"high: {tier_counts['high']}, medium: {tier_counts['medium']}, "
                f"low: {tier_counts['low']}. "
                f"Detected concerns: {', '.join(sorted(all_concerns)) or 'none'}."
            ),
        ))

        result.summary = (
            f"Impact analysis complete. {len(impact_graph)} files scored. "
            f"Critical: {tier_counts['critical']}, High: {tier_counts['high']}, "
            f"Medium: {tier_counts['medium']}, Low: {tier_counts['low']}. "
            f"Overall risk: {overall_severity.value}. "
            f"Concerns: {', '.join(sorted(all_concerns)) or 'none'}."
        )
        log.info(
            "impact_analysis_complete",
            overall_severity=overall_severity.value,
            findings=len(result.findings),
        )
        return result

    def _raise_category_findings(
        self,
        changed_files: list[ChangedFile],
        result: AgentResult,
        log: Any,
    ) -> None:
        """
        Raise structural findings for specific high-risk file categories.

        Separate from per-node scoring so the logic is explicit and testable.
        """
        db_migrations = [cf.path for cf in changed_files if cf.category == AssetCategory.DATABASE_MIGRATION]
        iac_files     = [cf.path for cf in changed_files if cf.category == AssetCategory.INFRASTRUCTURE_AS_CODE]
        api_schemas   = [cf.path for cf in changed_files if cf.category == AssetCategory.API_SCHEMA]
        deps          = [cf.path for cf in changed_files if cf.category == AssetCategory.DEPENDENCY_MANIFEST]
        pipelines     = [cf.path for cf in changed_files if cf.category == AssetCategory.CI_CD_PIPELINE]

        if db_migrations:
            log.info("db_migrations_detected", files=db_migrations)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.HIGH,
                title="Database migration files present",
                description=(
                    f"{len(db_migrations)} DB migration file(s) detected: "
                    f"{', '.join(db_migrations)}. "
                    "Schema changes require DBA sign-off and a tested rollback script."
                ),
                affected_assets=db_migrations,
                remediation_guidance=(
                    "Confirm the migration is reversible. Attach the rollback script "
                    "to the PR. Verify the migration has been tested against a prod-like dataset."
                ),
            ))

        if iac_files:
            log.info("iac_changes_detected", files=iac_files)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.HIGH,
                title="Infrastructure-as-Code changes present",
                description=(
                    f"{len(iac_files)} IaC file(s) modified: {', '.join(iac_files)}. "
                    "IaC changes can affect live infrastructure. Requires ops/platform review."
                ),
                affected_assets=iac_files,
                remediation_guidance=(
                    "Run terraform plan / equivalent dry-run and attach output to PR. "
                    "Confirm blast radius with the platform team."
                ),
            ))

        if api_schemas:
            log.info("api_schema_changes_detected", files=api_schemas)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.HIGH,
                title="API schema files modified",
                description=(
                    f"API/contract files changed: {', '.join(api_schemas)}. "
                    "Consumers depending on this contract may be broken."
                ),
                affected_assets=api_schemas,
                remediation_guidance=(
                    "Run contract compatibility tests. Notify all known consumers. "
                    "Consider publishing a changelog entry."
                ),
            ))

        if deps:
            log.info("dependency_changes_detected", files=deps)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.MEDIUM,
                title="Dependency manifest changes",
                description=(
                    f"Dependency file(s) changed: {', '.join(deps)}. "
                    "New or updated packages introduce supply-chain risk."
                ),
                affected_assets=deps,
                remediation_guidance=(
                    "Run SCA scan on updated dependencies. Confirm no known CVEs. "
                    "Pin versions in requirements/pyproject.toml."
                ),
            ))

        if pipelines:
            log.info("pipeline_changes_detected", files=pipelines)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.MEDIUM,
                title="CI/CD pipeline files modified",
                description=(
                    f"Pipeline file(s) changed: {', '.join(pipelines)}. "
                    "Pipeline modifications can affect build, test, and deploy integrity."
                ),
                affected_assets=pipelines,
                remediation_guidance=(
                    "Review pipeline changes for injection risks (e.g. untrusted input in "
                    "shell steps). Ensure no secrets are printed to logs."
                ),
            ))
