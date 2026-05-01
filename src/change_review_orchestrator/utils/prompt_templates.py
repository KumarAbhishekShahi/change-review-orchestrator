"""
LLM Prompt Templates — Change Review Orchestrator

All prompts used by the LLM Narrative agent are centralised here.
Each function returns a fully-rendered string ready for the Gemini client.

Design principles:
- Role + context + task + format = structured prompts
- Explicit output format instructions to ensure parseable responses
- Conservative temperature (0.2) to reduce hallucination
- Every prompt includes a fallback instruction:
  "If you are uncertain, say so rather than inventing details."
"""

from __future__ import annotations

from change_review_orchestrator.domain.models import Finding, WorkflowState


def threat_narrative_prompt(
    state: WorkflowState,
    security_findings: list[Finding],
    concerns: list[str],
) -> str:
    """
    Prompt to generate a concise threat narrative for the security section.

    Output: 3-5 sentences in plain English describing the key threat
    scenarios and their potential business impact.
    """
    case = state.case
    finding_lines = "\n".join(
        f"- [{f.severity.value}] {f.title}: {f.description[:200]}"
        for f in security_findings[:8]
    )
    concern_text = ", ".join(concerns) if concerns else "none detected"

    return f"""You are a senior application security engineer reviewing a code change for a banking system.

## Change Context
- Repository: {case.repository or "banking-platform"}
- Change Type: {case.change_type.value}
- Data Classification: {case.data_classification.value}
- Files Changed: {case.total_files_changed}
- Breaking Changes: {case.has_breaking_changes}

## Security Concerns Detected (by static analysis)
{concern_text}

## Security Findings
{finding_lines}

## Your Task
Write a concise threat narrative (3-5 sentences) that:
1. Describes the most significant attack scenarios this change introduces or worsens
2. Explains the potential business impact in banking terms (e.g. payment fraud, data breach, regulatory fine)
3. Highlights which finding(s) should be prioritised and why

## Output Format
Plain prose only. No bullet points. No markdown headers. 3-5 sentences maximum.
If you are uncertain about any claim, say so rather than inventing details.
Start directly with the threat narrative — no preamble.
"""


def executive_summary_prompt(
    state: WorkflowState,
    recommendation: str,
    composite_score: int,
    required_actions: list[str],
    finding_counts: dict[str, int],
) -> str:
    """
    Prompt to generate a crisp executive summary for the change review report.

    Output: 2-3 sentences for a non-technical senior stakeholder (CISO / Head of Engineering).
    """
    case = state.case
    actions_text = "\n".join(f"- {a}" for a in required_actions[:5])

    return f"""You are a principal engineer writing an executive summary of a change review for a banking system.
The audience is a non-technical senior stakeholder (e.g. CISO, Head of Engineering).

## Change
- Title: {case.title}
- Author: {case.author or "unknown"}
- Change Type: {case.change_type.value}
- Data Classification: {case.data_classification.value}

## Review Outcome
- Recommendation: {recommendation}
- Composite Risk Score: {composite_score}/100 (lower = more risk)
- Critical Findings: {finding_counts.get("critical", 0)}
- High Findings: {finding_counts.get("high", 0)}

## Required Actions Before Approval
{actions_text or "None"}

## Your Task
Write an executive summary of 2-3 sentences that:
1. States the recommendation and score in plain language
2. Mentions the most critical risk if any exists
3. States what must happen before this change can proceed (if anything)

## Output Format
Plain prose only. 2-3 sentences. No bullet points. No markdown.
Write for a reader who will spend 15 seconds on this.
If uncertain, say so rather than inventing details.
"""


def remediation_enrichment_prompt(
    finding_title: str,
    finding_description: str,
    existing_remediation: str,
    change_type: str,
    data_classification: str,
) -> str:
    """
    Prompt to enrich a finding's remediation guidance with actionable steps.

    Output: 2-4 bullet points with concrete, banking-specific remediation steps.
    """
    return f"""You are a senior security architect at a bank providing remediation guidance for a code review finding.

## Finding
- Title: {finding_title}
- Description: {finding_description[:400]}
- Current Guidance: {existing_remediation or "none provided"}
- Change Type: {change_type}
- Data Classification: {data_classification}

## Your Task
Enrich the remediation guidance with 2-4 concrete, actionable steps specific to a banking environment.
Focus on:
- The specific code fix or configuration change required
- Any compliance requirement this addresses (PCI-DSS, SOX, GDPR as applicable)
- Verification step to confirm the fix is effective

## Output Format
Return exactly 2-4 bullet points starting with "- ".
Each bullet must be actionable (start with a verb: "Update", "Remove", "Configure", etc.).
No preamble. No headers. No more than 4 bullets total.
If you are uncertain about any specific detail, omit that bullet rather than guessing.
"""


def change_summary_prompt(state: WorkflowState) -> str:
    """
    Prompt to generate a plain-English summary of what the change does.

    Output: 2-3 sentences describing the change for a reviewer unfamiliar with it.
    """
    case = state.case
    file_lines = "\n".join(
        f"- {cf.path} (+{cf.lines_added}/-{cf.lines_removed}) [{cf.category.value}]"
        for cf in case.changed_files[:10]
    )

    return f"""You are a senior engineer summarising a code change for a technical reviewer.

## Change Metadata
- Title: {case.title}
- Type: {case.change_type.value}
- Author: {case.author or "unknown"}
- JIRA: {case.jira_ticket or "not linked"}

## Changed Files
{file_lines}

## Your Task
Write a 2-3 sentence plain-English summary of what this change does.
Base your summary only on the file names, types, and the change title above.

## Output Format
Plain prose. 2-3 sentences. No lists. No headers.
Do not speculate beyond what the file names and metadata suggest.
If you are uncertain, describe what the files suggest rather than asserting specific implementation details.
"""
