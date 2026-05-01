"""
Security Agent — Change Review Orchestrator

Responsibilities:
1. Run mock SAST scan across all changed files
2. Apply rule-based security checks (auth, crypto, injection, secrets, supply-chain)
3. Cross-reference findings against impact graph concerns
4. Generate threat hypotheses for high-risk concern combinations
5. Produce structured security findings with CWE references
6. Summarise security posture and recommended review gates

Design: mock SAST scanner + deterministic rule-based checks.
LLM-enhanced threat narrative is wired in Step 11.
"""

from __future__ import annotations

import re
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
    EvidenceItem,
    Finding,
    WorkflowState,
)
from change_review_orchestrator.integrations.mock.sast_mock import MockSASTScanner

logger = structlog.get_logger(__name__)

# Severity mapping from SAST result strings → domain Severity
_SEV_MAP: dict[str, Severity] = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH":     Severity.HIGH,
    "MEDIUM":   Severity.MEDIUM,
    "LOW":      Severity.LOW,
    "INFO":     Severity.INFO,
}

# ── Rule-based security check patterns ───────────────────────────────────────

# Each entry: (check_id, pattern, severity, title, description, remediation)
_SECURITY_RULES: list[tuple[str, re.Pattern[str], Severity, str, str, str]] = [
    (
        "SEC-R-001",
        re.compile(r"(password|passwd|pwd|secret|api.?key|token)\s*=\s*[\"'][^\"']{4,}", re.I),
        Severity.CRITICAL,
        "Potential hardcoded secret in file path",
        "File path or name suggests hardcoded credentials or secrets.",
        "Move secrets to a vault or environment variable; never commit credentials.",
    ),
    (
        "SEC-R-002",
        re.compile(r"(eval|exec|os\.system|subprocess\.call|shell=True)", re.I),
        Severity.HIGH,
        "Dangerous code execution pattern in path",
        "File name suggests use of eval/exec/shell=True which can enable code injection.",
        "Avoid dynamic code execution. Use parameterised APIs instead.",
    ),
    (
        "SEC-R-003",
        re.compile(r"(ssl_verify\s*=\s*False|verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False)", re.I),
        Severity.CRITICAL,
        "TLS/SSL verification disabled pattern",
        "Pattern matches disabled TLS certificate verification.",
        "Never disable TLS verification in production. Pin certificates or use a trusted CA.",
    ),
    (
        "SEC-R-004",
        re.compile(r"(md5|sha1|des|rc4|ecb)", re.I),
        Severity.HIGH,
        "Weak cryptographic algorithm in file path",
        "File path references a weak or deprecated cryptographic algorithm.",
        "Replace MD5/SHA1/DES/RC4/ECB with SHA-256, AES-GCM, or ChaCha20-Poly1305.",
    ),
    (
        "SEC-R-005",
        re.compile(r"(cors|cross.?origin|access.?control.?allow)", re.I),
        Severity.MEDIUM,
        "CORS configuration change detected",
        "File touches CORS/cross-origin settings — misconfiguration can expose APIs.",
        "Explicitly whitelist allowed origins. Avoid wildcard '*' in production.",
    ),
]

# Threat hypotheses generated when specific concern combinations are found
_THREAT_HYPOTHESES: list[tuple[frozenset[str], str, str]] = [
    (
        frozenset({"auth/authz", "crypto/tls"}),
        "Authentication bypass via crypto weakness",
        "Simultaneous changes to auth and crypto modules raise the risk that a weakened "
        "cryptographic primitive could be leveraged to forge tokens or bypass authentication.",
    ),
    (
        frozenset({"payment/pci", "interface/api/schema"}),
        "PCI data exposure via API contract change",
        "API schema changes affecting payment flows may inadvertently expose PAN, CVV, "
        "or token data to unauthorised consumers if contract validation is loosened.",
    ),
    (
        frozenset({"auth/authz", "audit/compliance"}),
        "Audit trail tampering risk",
        "Concurrent changes to authorisation and audit logging raise the risk that "
        "privilege escalation could go undetected if log redaction removes evidence.",
    ),
    (
        frozenset({"interface/api/schema", "breaking-change"}),
        "Uncontrolled API surface expansion",
        "Breaking API changes combined with new interface additions may leave "
        "deprecated endpoints accessible without proper authorisation gates.",
    ),
]


class SecurityAgent(BaseAgent):
    """
    Security Agent.

    Runs SAST scan, applies rule-based checks, cross-references the impact
    graph concern set, generates threat hypotheses, and produces structured
    security findings with CWE references.
    """

    agent_name = "security"

    def __init__(self) -> None:
        self._scanner = MockSASTScanner()

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("security_analysis_started", files=len(case.changed_files))

        file_paths = [cf.path for cf in case.changed_files]

        # Pull impact concerns for threat hypothesis generation
        impact_result = state.agent_results.get("impact")
        concerns: set[str] = set()
        if impact_result and "all_concerns" in impact_result.metadata:
            concerns = set(impact_result.metadata["all_concerns"])

        # ── Step 1: Mock SAST scan ────────────────────────────────────────────
        sast_results = self._scanner.scan_files(file_paths)
        sast_finding_count = 0

        for sr in sast_results:
            sev = _SEV_MAP.get(sr["severity"], Severity.MEDIUM)
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.SECURITY,
                severity=sev,
                title=f"[SAST {sr['rule_id']}] {sr['title']}",
                description=(
                    f"{sr['description']} "
                    f"Detected in: {sr['affected_file']}. "
                    f"CWE: {sr['cwe']}."
                ),
                affected_assets=[sr["affected_file"]],
                remediation_guidance=sr["remediation"],
                policy_reference=sr["rule_id"],
            ))
            sast_finding_count += 1
            log.info("sast_finding_added", rule=sr["rule_id"], sev=sr["severity"],
                     file=sr["affected_file"])

        result.metadata["sast_findings_count"] = sast_finding_count
        result.metadata["sast_rules_triggered"] = [sr["rule_id"] for sr in sast_results]

        # ── Step 2: Rule-based path analysis ─────────────────────────────────
        rule_hits = 0
        for check_id, pattern, sev, title, desc, remediation in _SECURITY_RULES:
            matched_files = [fp for fp in file_paths if pattern.search(fp)]
            if matched_files:
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.SECURITY,
                    severity=sev,
                    title=f"[{check_id}] {title}",
                    description=f"{desc} Matched files: {', '.join(matched_files)}.",
                    affected_assets=matched_files,
                    remediation_guidance=remediation,
                    policy_reference=check_id,
                ))
                rule_hits += 1
                log.debug("security_rule_hit", check=check_id, files=matched_files)

        result.metadata["rule_based_hits"] = rule_hits

        # ── Step 3: Credential file check (from intake) ───────────────────────
        cred_files = [
            cf.path for cf in case.changed_files
            if cf.category == AssetCategory.SECRET_OR_CREDENTIAL
        ]
        if cred_files:
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.SECURITY,
                severity=Severity.CRITICAL,
                title="Credential or key files present in changeset",
                description=(
                    f"The following files are classified as credentials/secrets and must NOT "
                    f"appear in source control: {', '.join(cred_files)}."
                ),
                affected_assets=cred_files,
                remediation_guidance=(
                    "Remove from PR immediately. Rotate any exposed secrets. "
                    "Add these file patterns to .gitignore."
                ),
            ))

        # ── Step 4: Threat hypotheses ─────────────────────────────────────────
        threat_count = 0
        all_concern_labels = concerns | {
            node["impact_tier"] for node in (
                impact_result.metadata.get("impact_graph", []) if impact_result else []
            )
        }

        for required_concerns, hyp_title, hyp_desc in _THREAT_HYPOTHESES:
            if required_concerns.issubset(concerns):
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.SECURITY,
                    severity=Severity.HIGH,
                    title=f"[Threat Hypothesis] {hyp_title}",
                    description=hyp_desc,
                    affected_assets=[],
                    remediation_guidance=(
                        "Conduct a focused threat model review of the interaction "
                        "between the affected components before merging."
                    ),
                ))
                threat_count += 1
                log.warning("threat_hypothesis_raised", title=hyp_title, concerns=sorted(required_concerns))

        result.metadata["threat_hypotheses_count"] = threat_count

        # ── Step 5: Security posture summary ──────────────────────────────────
        total_sec = len(result.findings)
        critical_count = sum(1 for f in result.findings if f.severity == Severity.CRITICAL)
        high_count = sum(1 for f in result.findings if f.severity == Severity.HIGH)

        posture = (
            "CRITICAL" if critical_count > 0
            else "HIGH" if high_count > 0
            else "MEDIUM" if total_sec > 0
            else "CLEAR"
        )

        result.metadata["security_posture"] = posture
        result.metadata["critical_count"] = critical_count
        result.metadata["high_count"] = high_count

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Security Scan Report",
            content_summary=(
                f"SAST: {sast_finding_count} findings. "
                f"Rule-based checks: {rule_hits} hits. "
                f"Threat hypotheses: {threat_count}. "
                f"Overall posture: {posture}. "
                f"Critical: {critical_count}, High: {high_count}."
            ),
        ))

        result.summary = (
            f"Security analysis complete. Posture: {posture}. "
            f"Total findings: {total_sec} "
            f"(SAST: {sast_finding_count}, rules: {rule_hits}, "
            f"threats: {threat_count}). "
            f"Critical: {critical_count}, High: {high_count}."
        )

        log.info(
            "security_analysis_complete",
            posture=posture,
            total_findings=total_sec,
            critical=critical_count,
            high=high_count,
        )
        return result
