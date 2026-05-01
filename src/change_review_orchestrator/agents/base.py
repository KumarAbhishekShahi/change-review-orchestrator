"""
Abstract base class for all Change Review Orchestrator agents.

Every agent persona (Intake, Impact, Policy, etc.) inherits from BaseAgent
and implements a single `run()` method. The base class handles:
- Lifecycle logging (entry / exit / duration)
- AgentResult scaffolding (status, timestamps)
- Uniform error handling so one bad agent cannot crash the pipeline

Design principle: agents are stateless functions wrapped in a class.
They read from WorkflowState, produce an AgentResult, and return an
updated WorkflowState. They must NOT write to external systems directly —
that belongs in integration adapters.
"""

from __future__ import annotations

import datetime
import traceback
from abc import ABC, abstractmethod

import structlog

from change_review_orchestrator.domain.enums import AgentStatus, CaseStatus
from change_review_orchestrator.domain.models import AgentResult, WorkflowState

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for all agent personas.

    Subclasses must implement `_execute()`. The public `run()` method
    wraps execution with logging, timing, and error recovery.
    """

    # Subclasses set this to their human-readable name, e.g. "intake"
    agent_name: str = "base"

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute this agent and return an updated WorkflowState.

        This method is called by the LangGraph node wrapper. It:
        1. Initialises an AgentResult with RUNNING status
        2. Delegates to _execute() for agent-specific logic
        3. Marks the result COMPLETED or FAILED
        4. Merges findings/evidence into the shared state collections
        5. Returns the mutated state

        Args:
            state: The current shared workflow state.

        Returns:
            The updated workflow state with this agent's result recorded.
        """
        log = logger.bind(agent=self.agent_name, case_id=state.case.case_id)
        log.info("agent_starting")

        result = AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.RUNNING,
            started_at=datetime.datetime.now(datetime.timezone.utc),
        )

        try:
            result = self._execute(state, result)
            result.status = AgentStatus.COMPLETED
            result.completed_at = datetime.datetime.now(datetime.timezone.utc)

            log.info(
                "agent_completed",
                duration_s=result.duration_seconds,
                findings=len(result.findings),
                evidence_items=len(result.evidence_items),
                max_severity=result.max_severity.value if result.max_severity else "none",
            )

        except Exception as exc:  # noqa: BLE001
            # Catch all exceptions so a single agent failure does not abort
            # the entire pipeline. The error is recorded and the pipeline
            # supervisor decides whether to continue or halt.
            error_msg = f"{type(exc).__name__}: {exc}"
            tb = traceback.format_exc()
            result.status = AgentStatus.FAILED
            result.error_message = error_msg
            result.completed_at = datetime.datetime.now(datetime.timezone.utc)
            state.error_log.append(f"[{self.agent_name}] {error_msg}")

            log.error("agent_failed", error=error_msg, traceback=tb)

        # Persist the result and merge findings/evidence into shared state
        state.agent_results[self.agent_name] = result
        state.all_findings.extend(result.findings)
        state.all_evidence.extend(result.evidence_items)

        return state

    @abstractmethod
    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        """
        Agent-specific logic. Implemented by each subclass.

        Implementations should:
        - Read only from `state` (treat it as read-only input)
        - Populate `result.findings`, `result.evidence_items`, `result.summary`
        - Populate `result.metadata` for any agent-specific structured output
        - Return the populated `result`

        Args:
            state:  Current workflow state (read input).
            result: Pre-initialised AgentResult to populate.

        Returns:
            The populated AgentResult.
        """
        ...
