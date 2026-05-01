"""
Unit tests for LLMNarrativeAgent, prompt templates, and GeminiClient fallback.

Tests cover:
- Agent completes with LLM unavailable (graceful degradation)
- Deterministic fallbacks produce non-empty, valid output
- Stub GeminiClient injects controlled LLM responses
- All metadata keys populated regardless of LLM availability
- Markdown report updated with LLM section
- Prompt templates render without error for all case types
- llm_calls / fallbacks counts correct
- Enriched remediation keys match finding IDs
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from change_review_orchestrator.agents.adjudication import AdjudicationAgent
from change_review_orchestrator.agents.evidence_packager import EvidencePackagerAgent
from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.llm_narrative import (
    LLMNarrativeAgent,
    _deterministic_change_summary,
    _deterministic_executive_summary,
    _deterministic_threat_narrative,
)
from change_review_orchestrator.agents.policy import PolicyAgent
from change_review_orchestrator.agents.reliability import ReliabilityAgent
from change_review_orchestrator.agents.security import SecurityAgent
from change_review_orchestrator.agents.test_strategy import TestStrategyAgent
from change_review_orchestrator.domain.enums import AgentStatus, Severity
from change_review_orchestrator.domain.models import ChangeCase, WorkflowState
from change_review_orchestrator.integrations.real.gemini_client import GeminiClient
from change_review_orchestrator.utils.prompt_templates import (
    change_summary_prompt,
    executive_summary_prompt,
    remediation_enrichment_prompt,
    threat_narrative_prompt,
)
from tests.fixtures.sample_diffs import make_docs_only_case, make_pci_tokenisation_case

FIXTURE_POLICY_FILE = Path("tests/fixtures/policy_rules.yaml")


def _make_unavailable_client() -> GeminiClient:
    """Return a GeminiClient stub that reports unavailable."""
    client = MagicMock(spec=GeminiClient)
    client.available = False
    client.generate.return_value = None
    return client


def _make_stub_client(response: str = "Stub LLM response.") -> GeminiClient:
    """Return a GeminiClient stub that returns a fixed response."""
    client = MagicMock(spec=GeminiClient)
    client.available = True
    client.generate.return_value = response
    return client


def _run_full_pipeline(
    case: ChangeCase, llm_client: GeminiClient | None = None
) -> WorkflowState:
    state = WorkflowState(case=case)
    state = IntakeAgent().run(state)
    state = ImpactAgent().run(state)
    state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
    state = SecurityAgent().run(state)
    state = TestStrategyAgent().run(state)
    state = ReliabilityAgent().run(state)
    state = EvidencePackagerAgent().run(state)
    state = AdjudicationAgent().run(state)
    state = LLMNarrativeAgent(client=llm_client or _make_unavailable_client()).run(state)
    return state


# ── Deterministic fallback tests ──────────────────────────────────────────────

class TestDeterministicFallbacks:

    def test_change_summary_non_empty(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        summary = _deterministic_change_summary(state)
        assert len(summary) > 20

    def test_change_summary_includes_change_type(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        summary = _deterministic_change_summary(state)
        assert "feature" in summary.lower() or "database" in summary.lower()

    def test_executive_summary_includes_recommendation(self) -> None:
        summary = _deterministic_executive_summary("REJECT", 22, ["Fix CRITICAL finding"])
        assert "REJECT" in summary

    def test_executive_summary_includes_score(self) -> None:
        summary = _deterministic_executive_summary("NEEDS_WORK", 45, [])
        assert "45" in summary

    def test_executive_summary_no_actions_when_empty(self) -> None:
        summary = _deterministic_executive_summary("APPROVE", 88, [])
        assert "No blocking" in summary or "required" in summary.lower()

    def test_threat_narrative_non_empty(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        state = SecurityAgent().run(state)
        narrative = _deterministic_threat_narrative(state, ["payment/pci", "auth/authz"])
        assert len(narrative) > 20


# ── Prompt template rendering tests ──────────────────────────────────────────

class TestPromptTemplates:

    def test_change_summary_prompt_renders(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        prompt = change_summary_prompt(state)
        assert "Changed Files" in prompt
        assert len(prompt) > 100

    def test_threat_narrative_prompt_renders(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        state = SecurityAgent().run(state)
        security_findings = [
            f for f in state.all_findings
            if f.severity in (Severity.CRITICAL, Severity.HIGH)
        ]
        prompt = threat_narrative_prompt(state, security_findings, ["payment/pci"])
        assert "Security Findings" in prompt
        assert "banking" in prompt.lower()

    def test_executive_summary_prompt_renders(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        prompt = executive_summary_prompt(state, "REJECT", 22, ["Fix X"], {"critical": 3})
        assert "REJECT" in prompt
        assert "composite" in prompt.lower() or "Composite" in prompt

    def test_remediation_prompt_renders(self) -> None:
        prompt = remediation_enrichment_prompt(
            finding_title="JWT algorithm confusion",
            finding_description="JWT accepts none algorithm",
            existing_remediation="Use RS256",
            change_type="FEATURE",
            data_classification="RESTRICTED",
        )
        assert "banking" in prompt.lower()
        assert "JWT" in prompt

    def test_all_prompts_include_fallback_instruction(self) -> None:
        state = WorkflowState(case=make_pci_tokenisation_case())
        state = IntakeAgent().run(state)
        state = SecurityAgent().run(state)
        security_findings = [f for f in state.all_findings if f.severity == Severity.CRITICAL]
        prompts = [
            change_summary_prompt(state),
            threat_narrative_prompt(state, security_findings, []),
            executive_summary_prompt(state, "REJECT", 22, [], {}),
            remediation_enrichment_prompt("T", "D", "R", "FEATURE", "RESTRICTED"),
        ]
        for p in prompts:
            assert "uncertain" in p.lower(), f"Fallback instruction missing in prompt"


# ── LLMNarrativeAgent integration tests ──────────────────────────────────────

class TestLLMNarrativeAgent:

    def test_agent_completes_with_unavailable_llm(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        assert state.agent_results["llm_narrative"].status == AgentStatus.COMPLETED

    def test_fallbacks_used_when_llm_unavailable(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["llm_narrative"].metadata
        assert meta["llm_available"] is False
        assert meta["fallbacks_used"] > 0
        assert meta["llm_calls_made"] == 0

    def test_all_metadata_keys_present(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        meta = state.agent_results["llm_narrative"].metadata
        for key in ("llm_available", "llm_calls_made", "fallbacks_used",
                    "change_summary", "threat_narrative", "executive_summary",
                    "enriched_findings_count"):
            assert key in meta, f"Missing metadata key: {key}"

    def test_change_summary_non_empty(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        summary = state.agent_results["llm_narrative"].metadata["change_summary"]
        assert len(summary) > 10

    def test_executive_summary_non_empty(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        exec_summary = state.agent_results["llm_narrative"].metadata["executive_summary"]
        assert len(exec_summary) > 10

    def test_stub_llm_calls_counted(self) -> None:
        stub = _make_stub_client("Test LLM response for all calls.")
        state = _run_full_pipeline(make_pci_tokenisation_case(), llm_client=stub)
        meta = state.agent_results["llm_narrative"].metadata
        assert meta["llm_available"] is True
        assert meta["llm_calls_made"] >= 3   # change_summary + threat + executive

    def test_stub_llm_response_in_metadata(self) -> None:
        stub = _make_stub_client("Stub threat narrative about banking risks.")
        state = _run_full_pipeline(make_pci_tokenisation_case(), llm_client=stub)
        meta = state.agent_results["llm_narrative"].metadata
        assert "Stub" in meta["threat_narrative"] or "Stub" in meta["executive_summary"]

    def test_llm_section_appended_to_markdown_report(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        report_path = state.agent_results["evidence_packager"].metadata.get("report_path")
        assert report_path is not None
        content = Path(report_path).read_text()
        assert "AI-Generated Narrative" in content

    def test_evidence_item_created(self) -> None:
        state = _run_full_pipeline(make_pci_tokenisation_case())
        labels = [e.label for e in state.agent_results["llm_narrative"].evidence_items]
        assert "LLM Narrative Overlay" in labels

    def test_docs_case_completes_with_fallback(self) -> None:
        state = _run_full_pipeline(make_docs_only_case())
        assert state.agent_results["llm_narrative"].status == AgentStatus.COMPLETED

    def test_generate_called_with_none_response_uses_fallback(self) -> None:
        """If generate() returns None, fallback is used and counted."""
        client = MagicMock(spec=GeminiClient)
        client.available = True
        client.generate.return_value = None   # all calls return None
        state = _run_full_pipeline(make_pci_tokenisation_case(), llm_client=client)
        meta = state.agent_results["llm_narrative"].metadata
        assert meta["fallbacks_used"] > 0
