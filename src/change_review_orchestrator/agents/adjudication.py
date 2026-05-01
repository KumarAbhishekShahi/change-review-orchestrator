"""
Adjudication Agent — Change Review Orchestrator

Responsibilities:
1. Consume all findings, agent metadata, and reliability/test/policy scores
2. Apply composite scoring across 5 dimensions:
   - Severity distribution (finding counts weighted by severity)
   - Policy compliance gap count and severity
   - Security posture
   - Test confidence score
   - Rollback viability and deployment risk
3. Apply escalation rules (mandatory blockers)
4. Compute a final composite score (0-100) — lower = more risk
5. Emit a final recommendation: APPROVE / APPROVE_WITH_CONDITIONS / NEEDS_WORK / REJECT
6. List required actions before approval (blockers) and advisory actions
7. Capture escalation decisions with reasons

Design: deterministic rule-based scoring + escalation. No LLM.
LLM narrative overlay added in Step 11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import (
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


# ── Scoring weights ────────────────────────────────────────────────────────
# Each dimension contributes a DEDUCTION from a perfect score of 100.

# Per-finding deductions
_SEV_DEDUCTIONS: dict[Severity, float] = {
    Severity.CRITICAL: 18.0,
    Severity.HIGH:      8.0,
    Severity.MEDIUM:    3.0,
    Severity.LOW:       0.5,
    Severity.INFO:      0.0,
}

# Dimension caps (max deduction per dimension, prevents one bad dimension from
# dominating and makes the score more readable)
_FINDING_DEDUCTION_CAP     = 35.0
_POLICY_DEDUCTION_CAP      = 20.0
_SECURITY_DEDUCTION_CAP    = 20.0
_TEST_DEDUCTION_CAP        = 15.0
_RELIABILITY_DEDUCTION_CAP = 10.0


@dataclass
class EscalationRule:
    rule_id: str
    description: str
    triggered: bool
    severity: Severity
    required_action: str


def _compute_finding_deduction(findings: list[Finding]) -> float:
    """Sum severity-weighted deductions, capped at _FINDING_DEDUCTION_CAP."""
    total = sum(_SEV_DEDUCTIONS.get(f.severity, 0) for f in findings if not f.suppressed)
    return min(total, _FINDING_DEDUCTION_CAP)


def _compute_policy_deduction(policy_meta: dict[str, Any]) -> float:
    """
    Deduct points for policy gaps.
    Critical gaps = 10 pts each; High = 6; Medium = 3; capped at cap.
    """
    gaps: list[dict[str, Any]] = policy_meta.get("gaps", [])
    total = 0.0
    for gap in gaps:
        sev = gap.get("severity", "MEDIUM")
        if sev == "CRITICAL":
            total += 10
        elif sev == "HIGH":
            total += 6
        else:
            total += 3
    return min(total, _POLICY_DEDUCTION_CAP)


def _compute_security_deduction(security_meta: dict[str, Any]) -> float:
    """
    Deduct based on security posture rating.
    CRITICAL=20, HIGH=12, MEDIUM=6, CLEAR=0.
    """
    posture = security_meta.get("security_posture", "CLEAR")
    deductions = {"CRITICAL": 20.0, "HIGH": 12.0, "MEDIUM": 6.0, "CLEAR": 0.0}
    raw = deductions.get(posture, 6.0)
    return min(raw, _SECURITY_DEDUCTION_CAP)


def _compute_test_deduction(ts_meta: dict[str, Any]) -> float:
    """
    Deduct based on overall_confidence_score (0-100).
    Full confidence (100) = 0 deduction. Zero confidence = 15 deduction.
    """
    confidence = ts_meta.get("overall_confidence_score", 50)
    raw = (1 - confidence / 100) * _TEST_DEDUCTION_CAP
    return min(raw, _TEST_DEDUCTION_CAP)


def _compute_reliability_deduction(rel_meta: dict[str, Any]) -> float:
    """
    Deduct based on deployment_risk_score and rollback_viable.
    High deployment risk + non-viable rollback = max deduction.
    """
    risk = rel_meta.get("deployment_risk_score", 30)
    rollback_viable = rel_meta.get("rollback_viable", True)
    raw = (risk / 100) * _RELIABILITY_DEDUCTION_CAP
    if not rollback_viable:
        raw += 5
    return min(raw, _RELIABILITY_DEDUCTION_CAP)


def _apply_escalation_rules(
    findings: list[Finding],
    policy_meta: dict[str, Any],
    security_meta: dict[str, Any],
    rel_meta: dict[str, Any],
    ts_meta: dict[str, Any],
) -> list[EscalationRule]:
    """
    Apply mandatory escalation rules that can force REJECT regardless of score.

    Escalation rules are hard blockers — any triggered rule forces at minimum
    a NEEDS_WORK recommendation and may force REJECT.
    """
    rules: list[EscalationRule] = []

    # ESC-001: Any CRITICAL finding present
    critical_findings = [f for f in findings if f.severity == Severity.CRITICAL and not f.suppressed]
    rules.append(EscalationRule(
        rule_id="ESC-001",
        description="One or more CRITICAL severity findings present",
        triggered=len(critical_findings) > 0,
        severity=Severity.CRITICAL,
        required_action=(
            f"Resolve all {len(critical_findings)} CRITICAL finding(s) before approval: "
            + "; ".join(f.title for f in critical_findings[:3])
            + ("..." if len(critical_findings) > 3 else "")
        ) if critical_findings else "No critical findings.",
    ))

    # ESC-002: PCI policy gap unresolved
    pci_gaps = [
        g for g in policy_meta.get("gaps", [])
        if "POL-PCI" in g.get("rule_id", "")
    ]
    rules.append(EscalationRule(
        rule_id="ESC-002",
        description="PCI-DSS policy gap(s) unresolved",
        triggered=len(pci_gaps) > 0,
        severity=Severity.CRITICAL,
        required_action=(
            f"Resolve PCI-DSS policy gaps: {', '.join(g['rule_id'] for g in pci_gaps)}."
        ) if pci_gaps else "No PCI policy gaps.",
    ))

    # ESC-003: Non-viable rollback
    rollback_viable = rel_meta.get("rollback_viable", True)
    rollback_blockers = rel_meta.get("rollback_blockers", [])
    rules.append(EscalationRule(
        rule_id="ESC-003",
        description="Rollback not viable — deployment is irreversible",
        triggered=not rollback_viable,
        severity=Severity.HIGH,
        required_action=(
            "Provide a verified rollback plan addressing: "
            + "; ".join(rollback_blockers[:2])
        ) if rollback_blockers else "Rollback is viable.",
    ))

    # ESC-004: Security posture CRITICAL
    posture = security_meta.get("security_posture", "CLEAR")
    rules.append(EscalationRule(
        rule_id="ESC-004",
        description="Security posture is CRITICAL",
        triggered=posture == "CRITICAL",
        severity=Severity.CRITICAL,
        required_action="Resolve all CRITICAL security findings before merging.",
    ))

    # ESC-005: Zero test confidence
    confidence = ts_meta.get("overall_confidence_score", 100)
    rules.append(EscalationRule(
        rule_id="ESC-005",
        description="Test confidence score is critically low (< 30)",
        triggered=confidence < 30,
        severity=Severity.HIGH,
        required_action=f"Test confidence is {confidence}/100. Add mandatory test types before approval.",
    ))

    return rules


def _derive_recommendation(
    composite_score: int,
    escalation_rules: list[EscalationRule],
) -> tuple[str, str]:
    """
    Derive the final recommendation from composite score and escalation rules.

    Returns:
        (recommendation, rationale)

    Recommendation values:
        APPROVE                  — Score ≥ 80, no triggered escalations
        APPROVE_WITH_CONDITIONS  — Score 60–79, only non-critical escalations triggered
        NEEDS_WORK               — Score 40–59, or HIGH escalations triggered
        REJECT                   — Score < 40, or CRITICAL escalations triggered
    """
    triggered = [r for r in escalation_rules if r.triggered]
    critical_escalations = [r for r in triggered if r.severity == Severity.CRITICAL]
    high_escalations = [r for r in triggered if r.severity == Severity.HIGH]

    if critical_escalations or composite_score < 40:
        recommendation = "REJECT"
        rationale = (
            f"Change must not be merged. Composite score: {composite_score}/100. "
            f"Critical escalations: {', '.join(r.rule_id for r in critical_escalations)}."
        )
    elif high_escalations or composite_score < 60:
        recommendation = "NEEDS_WORK"
        rationale = (
            f"Significant issues must be resolved before this change can be approved. "
            f"Composite score: {composite_score}/100. "
            f"Issues: {', '.join(r.rule_id for r in (critical_escalations + high_escalations))}."
        )
    elif triggered or composite_score < 80:
        recommendation = "APPROVE_WITH_CONDITIONS"
        rationale = (
            f"Change may be approved after conditions are met. "
            f"Composite score: {composite_score}/100."
        )
    else:
        recommendation = "APPROVE"
        rationale = (
            f"Change meets all quality gates. Composite score: {composite_score}/100."
        )

    return recommendation, rationale


class AdjudicationAgent(BaseAgent):
    """
    Adjudication Agent.

    Applies composite scoring across 5 dimensions, evaluates escalation rules,
    and emits a final recommendation with a structured list of required and
    advisory actions.
    """

    agent_name = "adjudication"

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("adjudication_started", total_findings=len(state.all_findings))

        # ── Collect prior agent metadata ──────────────────────────────────────
        policy_meta   = state.agent_results.get("policy",       AgentResult("policy")).metadata
        security_meta = state.agent_results.get("security",     AgentResult("security")).metadata
        ts_meta       = state.agent_results.get("test_strategy",AgentResult("test_strategy")).metadata
        rel_meta      = state.agent_results.get("reliability",  AgentResult("reliability")).metadata

        findings = [f for f in state.all_findings if not f.suppressed]

        # ── Compute per-dimension deductions ──────────────────────────────────
        d_findings    = _compute_finding_deduction(findings)
        d_policy      = _compute_policy_deduction(policy_meta)
        d_security    = _compute_security_deduction(security_meta)
        d_test        = _compute_test_deduction(ts_meta)
        d_reliability = _compute_reliability_deduction(rel_meta)

        total_deduction = d_findings + d_policy + d_security + d_test + d_reliability
        composite_score = max(0, min(100, round(100 - total_deduction)))

        log.info(
            "composite_score_computed",
            score=composite_score,
            d_findings=d_findings,
            d_policy=d_policy,
            d_security=d_security,
            d_test=d_test,
            d_reliability=d_reliability,
        )

        # ── Apply escalation rules ────────────────────────────────────────────
        escalation_rules = _apply_escalation_rules(
            findings, policy_meta, security_meta, rel_meta, ts_meta
        )
        triggered_rules = [r for r in escalation_rules if r.triggered]

        # ── Derive recommendation ─────────────────────────────────────────────
        recommendation, rationale = _derive_recommendation(composite_score, escalation_rules)

        log.info(
            "recommendation_derived",
            recommendation=recommendation,
            composite_score=composite_score,
            triggered_escalations=[r.rule_id for r in triggered_rules],
        )

        # ── Build required actions (blockers) and advisory actions ────────────
        required_actions: list[str] = []
        advisory_actions: list[str] = []

        # Required: triggered escalation rules
        for rule in triggered_rules:
            required_actions.append(f"[{rule.rule_id}] {rule.required_action}")

        # Required: CRITICAL findings not linked to escalation rules
        critical_findings = [f for f in findings if f.severity == Severity.CRITICAL]
        for f in critical_findings:
            action = f"Resolve CRITICAL finding: {f.title}"
            if action not in required_actions:
                required_actions.append(action)

        # Advisory: HIGH findings
        high_findings = [f for f in findings if f.severity == Severity.HIGH]
        for f in high_findings[:5]:   # top 5
            advisory_actions.append(f"Address HIGH finding: {f.title}")

        # Advisory: deployment strategy recommendation
        strategy = rel_meta.get("deployment_strategy")
        if strategy and strategy != "standard":
            advisory_actions.append(
                f"Use recommended deployment strategy: {strategy}. "
                f"{rel_meta.get('strategy_rationale', '')}."
            )

        # Advisory: missing test types
        missing_tests = ts_meta.get("all_missing_test_types", [])
        if missing_tests:
            advisory_actions.append(
                f"Add missing test types: {', '.join(missing_tests)}."
            )

        # ── Persist structured metadata ───────────────────────────────────────
        result.metadata.update({
            "composite_score":       composite_score,
            "recommendation":        recommendation,
            "rationale":             rationale,
            "dimension_deductions": {
                "findings":    round(d_findings, 2),
                "policy":      round(d_policy, 2),
                "security":    round(d_security, 2),
                "test":        round(d_test, 2),
                "reliability": round(d_reliability, 2),
            },
            "escalation_rules": [
                {
                    "rule_id":     r.rule_id,
                    "triggered":   r.triggered,
                    "description": r.description,
                    "severity":    r.severity.value,
                }
                for r in escalation_rules
            ],
            "triggered_escalations":  [r.rule_id for r in triggered_rules],
            "required_actions":       required_actions,
            "advisory_actions":       advisory_actions,
        })

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Adjudication Decision",
            content_summary=(
                f"Recommendation: {recommendation}. "
                f"Composite score: {composite_score}/100. "
                f"Required actions: {len(required_actions)}. "
                f"Advisory actions: {len(advisory_actions)}. "
                f"Triggered escalations: {[r.rule_id for r in triggered_rules]}."
            ),
        ))

        result.summary = (
            f"Adjudication complete. Recommendation: {recommendation}. "
            f"Composite score: {composite_score}/100. "
            f"Required actions: {len(required_actions)}. "
            f"Rationale: {rationale}"
        )

        return result
