"""
Pipeline Runner — wires all agents in sequence and persists the result.

Called by the API route handler. Returns a completed WorkflowState.
In production this would be dispatched to a background worker (Celery/ARQ).
For now it runs synchronously so the API can return the result immediately
on the /sync endpoint, or queue it and poll via /reviews/{case_id}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from change_review_orchestrator.agents.adjudication import AdjudicationAgent
from change_review_orchestrator.agents.evidence_packager import EvidencePackagerAgent
from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.llm_narrative import LLMNarrativeAgent
from change_review_orchestrator.agents.policy import PolicyAgent
from change_review_orchestrator.agents.reliability import ReliabilityAgent
from change_review_orchestrator.agents.security import SecurityAgent
from change_review_orchestrator.agents.test_strategy import TestStrategyAgent
from change_review_orchestrator.domain.enums import AssetCategory, ChangeType, DataClassification
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile, WorkflowState

logger = structlog.get_logger(__name__)

_POLICY_FILE = Path(__file__).parent.parent.parent.parent.parent / "tests" / "fixtures" / "policy_rules.yaml"


def build_case_from_request(payload: dict[str, Any]) -> ChangeCase:
    """
    Convert a raw request payload dict into a ChangeCase domain object.
    Applies safe enum coercion with fallback to defaults.
    """
    def safe_enum(enum_cls, value, default):
        try:
            return enum_cls(value) if value else default
        except ValueError:
            return default

    changed_files = [
        ChangedFile(
            path=f.get("path", "unknown"),
            lines_added=f.get("lines_added", 0),
            lines_removed=f.get("lines_removed", 0),
            is_binary=f.get("is_binary", False),
            is_breaking_change=f.get("is_breaking_change", False),
        )
        for f in payload.get("changed_files", [])
    ]

    return ChangeCase(
        title=payload.get("title", "Untitled Change"),
        source_system=payload.get("source_system"),
        source_ref=payload.get("source_ref"),
        repository=payload.get("repository"),
        branch=payload.get("branch"),
        author=payload.get("author"),
        commit_sha=payload.get("commit_sha"),
        change_type=safe_enum(ChangeType, payload.get("change_type"), ChangeType.FEATURE),
        data_classification=safe_enum(
            DataClassification,
            payload.get("data_classification"),
            DataClassification.INTERNAL,
        ),
        jira_ticket=payload.get("jira_ticket"),
        release_version=payload.get("release_version"),
        reviewers=payload.get("reviewers", []),
        labels=payload.get("labels", []),
        description=payload.get("description"),
        has_breaking_changes=payload.get("has_breaking_changes", False),
        changed_files=changed_files,
    )


def run_pipeline(case: ChangeCase) -> WorkflowState:
    """
    Execute the full agent pipeline on a ChangeCase.

    Agent order:
      1. Intake         — classification, metadata extraction
      2. Impact         — risk scoring, concern detection
      3. Policy         — obligation matching, gap detection
      4. Security       — SAST scan, threat hypotheses
      5. Test Strategy  — coverage gap analysis
      6. Reliability    — deployment risk, rollback, blast radius
      7. Evidence Packager — Markdown + JSON artefact generation
      8. Adjudication   — composite scoring, final recommendation
      9. LLM Narrative  — AI-generated prose overlay (graceful degradation)

    Returns:
        Fully populated WorkflowState.
    """
    log = logger.bind(case_id=case.case_id)
    log.info("pipeline_start", title=case.title)

    state = WorkflowState(case=case)

    policy_file = _POLICY_FILE if _POLICY_FILE.exists() else None

    agents = [
        IntakeAgent(),
        ImpactAgent(),
        PolicyAgent(policy_file=policy_file),
        SecurityAgent(),
        TestStrategyAgent(),
        ReliabilityAgent(),
        EvidencePackagerAgent(),
        AdjudicationAgent(),
        LLMNarrativeAgent(),
    ]

    for agent in agents:
        try:
            state = agent.run(state)
            log.debug("agent_done", agent=agent.agent_name,
                      status=state.agent_results[agent.agent_name].status.value)
        except Exception as exc:
            log.error("agent_unhandled_exception", agent=agent.agent_name, error=str(exc))
            # Continue pipeline — individual agent errors are already captured

    log.info(
        "pipeline_complete",
        findings=len(state.all_findings),
        recommendation=state.agent_results.get("adjudication", {}).metadata.get("recommendation")
        if hasattr(state.agent_results.get("adjudication", {}), "metadata") else "unknown",
    )
    return state
