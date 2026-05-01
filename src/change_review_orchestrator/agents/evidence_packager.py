"""
Evidence Packager Agent — Change Review Orchestrator

Responsibilities:
1. Aggregate all findings from every agent into a deduplicated, sorted list
2. Group findings by severity and category for the report
3. Generate a structured Markdown review report
4. Produce a JSON audit bundle with full agent metadata
5. Persist both artefacts to the artefact store
6. Build a cross-agent evidence index for the Adjudication agent

Design: pure aggregation — no new analysis. Reads state, writes artefacts.
"""

from __future__ import annotations

import datetime
import json
from collections import defaultdict
from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import FindingCategory, Severity
from change_review_orchestrator.domain.models import (
    AgentResult,
    EvidenceItem,
    Finding,
    WorkflowState,
)
from change_review_orchestrator.integrations.mock.artifact_store_mock import (
    LocalFilesystemArtifactStore,
)

logger = structlog.get_logger(__name__)

_SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


def _severity_label(sev: Severity) -> str:
    icons = {
        Severity.CRITICAL: "🔴 CRITICAL",
        Severity.HIGH:     "🟠 HIGH",
        Severity.MEDIUM:   "🟡 MEDIUM",
        Severity.LOW:      "🟢 LOW",
        Severity.INFO:     "ℹ️  INFO",
    }
    return icons.get(sev, sev.value)


def _generate_markdown_report(
    state: WorkflowState,
    findings: list[Finding],
    generated_at: str,
) -> str:
    """Generate a structured Markdown review report from all agent outputs."""
    case = state.case
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        "# Change Review Report",
        "",
        f"**Case ID:** `{case.case_id}`  ",
        f"**Generated:** {generated_at}  ",
        f"**Source:** {case.source_system or 'n/a'} — {case.source_ref or 'n/a'}  ",
        f"**Repository:** `{case.repository or 'n/a'}`  ",
        f"**Branch:** `{case.branch or 'n/a'}`  ",
        f"**Author:** {case.author or 'n/a'}  ",
        f"**Change Type:** {case.change_type.value}  ",
        f"**Data Classification:** {case.data_classification.value}  ",
        f"**JIRA Ticket:** {case.jira_ticket or 'not linked'}  ",
        f"**Release Version:** {case.release_version or 'not specified'}  ",
        "",
        "---",
        "",
    ]

    # ── Executive Summary ──────────────────────────────────────────────────
    critical_count = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    high_count     = sum(1 for f in findings if f.severity == Severity.HIGH)
    medium_count   = sum(1 for f in findings if f.severity == Severity.MEDIUM)
    low_count      = sum(1 for f in findings if f.severity == Severity.LOW)

    lines += [
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Findings | **{len(findings)}** |",
        f"| 🔴 Critical | {critical_count} |",
        f"| 🟠 High | {high_count} |",
        f"| 🟡 Medium | {medium_count} |",
        f"| 🟢 Low | {low_count} |",
        f"| Files Changed | {case.total_files_changed} |",
        f"| Lines Added | {case.total_lines_added} |",
        f"| Lines Removed | {case.total_lines_removed} |",
        f"| Breaking Changes | {'Yes ⚠️' if case.has_breaking_changes else 'No'} |",
        "",
    ]

    # ── Agent Summaries ────────────────────────────────────────────────────
    lines += ["## Agent Summaries", ""]
    for agent_name, agent_result in state.agent_results.items():
        status_icon = "✅" if agent_result.status.value == "COMPLETED" else "❌"
        dur = f"{agent_result.duration_seconds:.1f}s" if agent_result.duration_seconds else "n/a"
        lines += [
            f"### {status_icon} {agent_name.replace('_', ' ').title()}",
            "",
            f"- **Status:** {agent_result.status.value}",
            f"- **Duration:** {dur}",
            f"- **Findings:** {len(agent_result.findings)}",
            f"- **Summary:** {agent_result.summary or '(no summary)'}",
            "",
        ]

    # ── Findings by Severity ───────────────────────────────────────────────
    lines += ["## Findings", ""]

    by_severity: dict[Severity, list[Finding]] = defaultdict(list)
    for f in findings:
        by_severity[f.severity].append(f)

    for sev in _SEV_ORDER:
        sev_findings = by_severity.get(sev, [])
        if not sev_findings:
            continue
        lines += [f"### {_severity_label(sev)} ({len(sev_findings)})", ""]
        for i, f in enumerate(sev_findings, 1):
            lines += [
                f"#### {i}. {f.title}",
                "",
                f"- **Agent:** {f.agent}",
                f"- **Category:** {f.category.value}",
            ]
            if f.policy_reference:
                lines.append(f"- **Policy/Rule Ref:** `{f.policy_reference}`")
            if f.affected_assets:
                lines.append(f"- **Affected:** {', '.join(f'`{a}`' for a in f.affected_assets)}")
            lines += [
                f"- **Description:** {f.description}",
                f"- **Remediation:** {f.remediation_guidance or 'See agent documentation.'}",
                "",
            ]

    # ── Reliability Assessment ─────────────────────────────────────────────
    rel_result = state.agent_results.get("reliability")
    if rel_result and rel_result.metadata:
        meta = rel_result.metadata
        lines += [
            "## Deployment Readiness",
            "",
            f"| Dimension | Score / Value |",
            f"|-----------|--------------|",
            f"| Deployment Risk | {meta.get('deployment_risk_score', 'n/a')}/100 |",
            f"| Rollback Viability | {'✅ Viable' if meta.get('rollback_viable') else '❌ Blocked'} |",
            f"| Rollback Score | {meta.get('rollback_score', 'n/a')}/100 |",
            f"| Observability | {meta.get('observability_score', 'n/a')}/100 |",
            f"| Blast Radius | {meta.get('blast_radius_score', 'n/a')}/100 ({meta.get('blast_consumers', 'n/a')}) |",
            f"| Recommended Strategy | **{meta.get('deployment_strategy', 'n/a')}** |",
            "",
            f"> {meta.get('strategy_rationale', '')}",
            "",
        ]

    # ── Test Strategy ──────────────────────────────────────────────────────
    ts_result = state.agent_results.get("test_strategy")
    if ts_result and ts_result.metadata:
        meta = ts_result.metadata
        lines += [
            "## Test Coverage",
            "",
            f"- **Overall Confidence:** {meta.get('overall_confidence_score', 'n/a')}/100",
            f"- **Coverage Gaps:** {meta.get('gap_count', 0)}",
            f"- **Missing Test Types:** {', '.join(meta.get('all_missing_test_types', [])) or 'none'}",
            "",
        ]

    # ── Evidence Index ─────────────────────────────────────────────────────
    lines += ["## Evidence Index", ""]
    for i, ev in enumerate(state.all_evidence, 1):
        lines.append(f"{i}. **[{ev.source_agent}]** {ev.label} — {ev.content_summary}")
    lines.append("")

    # ── Footer ─────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        f"*Report generated by Change Review Orchestrator at {generated_at}.*",
        "",
    ]

    return "\n".join(lines)


class EvidencePackagerAgent(BaseAgent):
    """
    Evidence Packager Agent.

    Aggregates all prior agent outputs into a Markdown review report and
    a JSON audit bundle. Both artefacts are persisted to the artefact store.
    Produces a cross-agent evidence index for the Adjudication agent.
    """

    agent_name = "evidence_packager"

    def __init__(self) -> None:
        self._store = LocalFilesystemArtifactStore()

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info(
            "packaging_started",
            total_findings=len(state.all_findings),
            total_evidence=len(state.all_evidence),
        )

        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # ── Sort findings: CRITICAL first, then by agent ──────────────────────
        sorted_findings = sorted(
            state.all_findings,
            key=lambda f: (_SEV_ORDER.index(f.severity), f.agent),
        )

        # ── Generate Markdown report ──────────────────────────────────────────
        md_report = _generate_markdown_report(state, sorted_findings, generated_at)
        md_path = self._store.write(case.case_id, "review_report.md", md_report)
        log.info("markdown_report_written", path=md_path, size=len(md_report))

        # ── Build JSON audit bundle ───────────────────────────────────────────
        audit_bundle: dict[str, Any] = {
            "case_id":       case.case_id,
            "generated_at":  generated_at,
            "pipeline_version": "1.0.0",
            "case_summary": {
                "title":              case.title,
                "source_ref":         case.source_ref,
                "repository":         case.repository,
                "change_type":        case.change_type.value,
                "data_classification": case.data_classification.value,
                "total_files":        case.total_files_changed,
                "has_breaking_changes": case.has_breaking_changes,
                "jira_ticket":        case.jira_ticket,
                "release_version":    case.release_version,
                "author":             case.author,
            },
            "finding_counts": {
                "total":    len(state.all_findings),
                "critical": sum(1 for f in state.all_findings if f.severity == Severity.CRITICAL),
                "high":     sum(1 for f in state.all_findings if f.severity == Severity.HIGH),
                "medium":   sum(1 for f in state.all_findings if f.severity == Severity.MEDIUM),
                "low":      sum(1 for f in state.all_findings if f.severity == Severity.LOW),
            },
            "findings": [
                {
                    "finding_id":       f.finding_id,
                    "agent":            f.agent,
                    "category":         f.category.value,
                    "severity":         f.severity.value,
                    "title":            f.title,
                    "description":      f.description,
                    "affected_assets":  f.affected_assets,
                    "policy_reference": f.policy_reference,
                    "remediation":      f.remediation_guidance,
                    "suppressed":       f.suppressed,
                }
                for f in sorted_findings
            ],
            "agent_results": {
                name: {
                    "status":   ar.status.value,
                    "summary":  ar.summary,
                    "duration_seconds": ar.duration_seconds,
                    "findings_count": len(ar.findings),
                    "metadata": ar.metadata,
                }
                for name, ar in state.agent_results.items()
            },
            "evidence_index": [
                {
                    "source_agent":   ev.source_agent,
                    "label":          ev.label,
                    "summary":        ev.content_summary,
                    "artifact_path":  ev.artifact_path,
                }
                for ev in state.all_evidence
            ],
        }

        audit_json = json.dumps(audit_bundle, indent=2, default=str)
        json_path = self._store.write(case.case_id, "audit_bundle.json", audit_json)
        log.info("audit_bundle_written", path=json_path, size=len(audit_json))

        # ── Populate result ───────────────────────────────────────────────────
        result.metadata["report_path"]  = md_path
        result.metadata["bundle_path"]  = json_path
        result.metadata["report_size_bytes"] = len(md_report)
        result.metadata["bundle_size_bytes"] = len(audit_json)
        result.metadata["findings_packaged"] = len(sorted_findings)

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Markdown Review Report",
            content_summary=f"Full review report — {len(sorted_findings)} findings, "
                            f"{len(md_report):,} bytes.",
            artifact_path=md_path,
        ))
        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="JSON Audit Bundle",
            content_summary=f"Complete audit bundle — {len(audit_json):,} bytes. "
                            f"Machine-readable for downstream compliance systems.",
            artifact_path=json_path,
        ))

        result.summary = (
            f"Evidence packaged. {len(sorted_findings)} findings across "
            f"{len(state.agent_results)} agents. "
            f"Report: {md_path}. Bundle: {json_path}."
        )
        log.info("packaging_complete", findings=len(sorted_findings))
        return result
