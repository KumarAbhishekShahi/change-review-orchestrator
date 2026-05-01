"""
LLM Narrative Overlay Agent — Change Review Orchestrator

Responsibilities:
1. Invoke Gemini API to generate:
   a. Plain-English change summary (what does this change do?)
   b. Threat narrative (security risk context for the CISO)
   c. Executive summary (recommendation + key risk for stakeholders)
   d. Enriched remediation guidance for top CRITICAL/HIGH findings
2. Append LLM-generated text to the Markdown report artefact
3. Degrade gracefully — if Gemini is unavailable, use deterministic fallbacks
4. Track token/cost metadata for observability

Design: wraps GeminiClient; all deterministic analysis already done
by Steps 3-10. This step enriches human-readable output only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import FindingCategory, Severity
from change_review_orchestrator.domain.models import (
    AgentResult,
    EvidenceItem,
    WorkflowState,
)
from change_review_orchestrator.integrations.real.gemini_client import GeminiClient
from change_review_orchestrator.utils.prompt_templates import (
    change_summary_prompt,
    executive_summary_prompt,
    remediation_enrichment_prompt,
    threat_narrative_prompt,
)

logger = structlog.get_logger(__name__)

# Max number of findings to enrich with LLM remediation
_MAX_ENRICHMENT_FINDINGS = 3


def _deterministic_change_summary(state: WorkflowState) -> str:
    """Fallback change summary when Gemini is unavailable."""
    case = state.case
    cats = {cf.category.value for cf in case.changed_files}
    return (
        f"This {case.change_type.value.lower().replace('_', ' ')} modifies "
        f"{case.total_files_changed} file(s) across categories: "
        f"{', '.join(sorted(cats))}. "
        f"{'Breaking changes are present. ' if case.has_breaking_changes else ''}"
        f"Data classification: {case.data_classification.value}."
    )


def _deterministic_threat_narrative(state: WorkflowState, concerns: list[str]) -> str:
    """Fallback threat narrative when Gemini is unavailable."""
    security_result = state.agent_results.get("security")
    posture = "UNKNOWN"
    if security_result:
        posture = security_result.metadata.get("security_posture", "UNKNOWN")
    concern_text = ", ".join(concerns) if concerns else "no specific concerns"
    return (
        f"Security posture assessed as {posture}. "
        f"Key concern areas: {concern_text}. "
        f"Review all CRITICAL and HIGH security findings before approving this change."
    )


def _deterministic_executive_summary(
    recommendation: str,
    composite_score: int,
    required_actions: list[str],
) -> str:
    """Fallback executive summary when Gemini is unavailable."""
    action_count = len(required_actions)
    return (
        f"The automated review recommends: {recommendation} "
        f"(composite risk score: {composite_score}/100). "
        f"{'There are ' + str(action_count) + ' required action(s) that must be resolved before this change can proceed.' if action_count else 'No blocking actions required.'}"
    )


class LLMNarrativeAgent(BaseAgent):
    """
    LLM Narrative Overlay Agent.

    Uses Gemini to produce human-readable narratives layered on top of the
    deterministic analysis from all prior agents. Degrades gracefully to
    deterministic fallbacks when the API is unavailable.
    """

    agent_name = "llm_narrative"

    def __init__(self, client: GeminiClient | None = None) -> None:
        """
        Args:
            client: Optional GeminiClient instance. Defaults to a new instance.
                    Pass a mock/stub client in tests.
        """
        self._client = client or GeminiClient()

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)

        llm_available = self._client.available
        log.info("llm_narrative_started", llm_available=llm_available)

        # Collect inputs from prior agents
        adjudication_meta = state.agent_results.get(
            "adjudication", AgentResult("adjudication")
        ).metadata
        security_result = state.agent_results.get("security")
        impact_result   = state.agent_results.get("impact")

        recommendation    = adjudication_meta.get("recommendation", "UNKNOWN")
        composite_score   = adjudication_meta.get("composite_score", 0)
        required_actions  = adjudication_meta.get("required_actions", [])
        concerns: list[str] = list(
            impact_result.metadata.get("all_concerns", []) if impact_result else []
        )
        security_findings = [
            f for f in state.all_findings
            if f.category == FindingCategory.SECURITY
            and f.severity in (Severity.CRITICAL, Severity.HIGH)
        ]

        finding_counts = {
            "critical": sum(1 for f in state.all_findings if f.severity == Severity.CRITICAL),
            "high":     sum(1 for f in state.all_findings if f.severity == Severity.HIGH),
            "medium":   sum(1 for f in state.all_findings if f.severity == Severity.MEDIUM),
            "low":      sum(1 for f in state.all_findings if f.severity == Severity.LOW),
        }

        llm_calls = 0
        fallbacks = 0

        # ── 1. Change Summary ─────────────────────────────────────────────────
        change_summary: str
        if llm_available:
            prompt = change_summary_prompt(state)
            change_summary = self._client.generate(prompt) or ""
            if change_summary:
                llm_calls += 1
            else:
                change_summary = _deterministic_change_summary(state)
                fallbacks += 1
        else:
            change_summary = _deterministic_change_summary(state)
            fallbacks += 1

        log.debug("change_summary_generated", source="llm" if llm_calls else "fallback")

        # ── 2. Threat Narrative ───────────────────────────────────────────────
        threat_narrative: str
        if llm_available and security_findings:
            prompt = threat_narrative_prompt(state, security_findings, concerns)
            threat_narrative = self._client.generate(prompt) or ""
            if threat_narrative:
                llm_calls += 1
            else:
                threat_narrative = _deterministic_threat_narrative(state, concerns)
                fallbacks += 1
        else:
            threat_narrative = _deterministic_threat_narrative(state, concerns)
            if not llm_available:
                fallbacks += 1

        # ── 3. Executive Summary ──────────────────────────────────────────────
        executive_summary: str
        if llm_available:
            prompt = executive_summary_prompt(
                state, recommendation, composite_score,
                required_actions, finding_counts,
            )
            executive_summary = self._client.generate(prompt) or ""
            if executive_summary:
                llm_calls += 1
            else:
                executive_summary = _deterministic_executive_summary(
                    recommendation, composite_score, required_actions
                )
                fallbacks += 1
        else:
            executive_summary = _deterministic_executive_summary(
                recommendation, composite_score, required_actions
            )
            fallbacks += 1

        # ── 4. Remediation Enrichment for top CRITICAL/HIGH findings ──────────
        top_findings = sorted(
            [f for f in state.all_findings if f.severity in (Severity.CRITICAL, Severity.HIGH)],
            key=lambda f: (f.severity == Severity.CRITICAL, f.agent),
            reverse=True,
        )[:_MAX_ENRICHMENT_FINDINGS]

        enriched_remediations: dict[str, str] = {}

        if llm_available:
            for finding in top_findings:
                prompt = remediation_enrichment_prompt(
                    finding_title=finding.title,
                    finding_description=finding.description,
                    existing_remediation=finding.remediation_guidance or "",
                    change_type=case.change_type.value,
                    data_classification=case.data_classification.value,
                )
                enriched = self._client.generate(prompt)
                if enriched:
                    enriched_remediations[finding.finding_id] = enriched
                    llm_calls += 1
                    log.debug("remediation_enriched", finding_id=finding.finding_id)
                else:
                    fallbacks += 1

        # ── 5. Append LLM section to Markdown report ─────────────────────────
        packager_meta = state.agent_results.get(
            "evidence_packager", AgentResult("evidence_packager")
        ).metadata
        report_path_str: str | None = packager_meta.get("report_path")

        if report_path_str:
            report_path = Path(report_path_str)
            if report_path.exists():
                existing = report_path.read_text(encoding="utf-8")
                source_label = "Gemini AI" if llm_available else "Deterministic Fallback"
                llm_section = _build_llm_section(
                    change_summary=change_summary,
                    threat_narrative=threat_narrative,
                    executive_summary=executive_summary,
                    enriched_remediations=enriched_remediations,
                    top_findings=top_findings,
                    source_label=source_label,
                )
                report_path.write_text(existing + "\n" + llm_section, encoding="utf-8")
                log.info("llm_section_appended_to_report", path=report_path_str)

        # ── Metadata + evidence ───────────────────────────────────────────────
        result.metadata.update({
            "llm_available":         llm_available,
            "llm_calls_made":        llm_calls,
            "fallbacks_used":        fallbacks,
            "change_summary":        change_summary,
            "threat_narrative":      threat_narrative,
            "executive_summary":     executive_summary,
            "enriched_findings_count": len(enriched_remediations),
            "enriched_finding_ids":  list(enriched_remediations.keys()),
        })

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="LLM Narrative Overlay",
            content_summary=(
                f"Source: {'Gemini' if llm_available else 'deterministic fallback'}. "
                f"LLM calls: {llm_calls}. Fallbacks: {fallbacks}. "
                f"Enriched remediations: {len(enriched_remediations)}."
            ),
        ))

        result.summary = (
            f"LLM narrative complete. "
            f"{'Gemini used.' if llm_available else 'Deterministic fallbacks used (Gemini unavailable).'} "
            f"LLM calls: {llm_calls}. Fallbacks: {fallbacks}. "
            f"Enriched remediation for {len(enriched_remediations)} finding(s)."
        )

        log.info(
            "llm_narrative_complete",
            llm_calls=llm_calls,
            fallbacks=fallbacks,
            enriched=len(enriched_remediations),
        )
        return result


def _build_llm_section(
    change_summary: str,
    threat_narrative: str,
    executive_summary: str,
    enriched_remediations: dict[str, str],
    top_findings: list[Any],
    source_label: str,
) -> str:
    """Build the LLM narrative Markdown section appended to the report."""
    lines: list[str] = [
        "",
        "---",
        "",
        f"## AI-Generated Narrative _{source_label}_",
        "",
        "### Change Summary",
        "",
        change_summary,
        "",
        "### Threat Narrative",
        "",
        threat_narrative,
        "",
        "### Executive Summary",
        "",
        executive_summary,
        "",
    ]

    if enriched_remediations:
        lines += ["### Enriched Remediation Guidance", ""]
        for finding in top_findings:
            if finding.finding_id in enriched_remediations:
                lines += [
                    f"#### {finding.title}",
                    "",
                    enriched_remediations[finding.finding_id],
                    "",
                ]

    return "\n".join(lines)
