"""
LangGraph workflow state definition.

LangGraph requires a TypedDict (or Pydantic model) as the state type.
We use a TypedDict that wraps the domain WorkflowState so LangGraph can
checkpoint and restore it between node executions.

The actual data lives in WorkflowState (Pydantic); this TypedDict is a
thin adapter that satisfies LangGraph's type contract.
"""

from __future__ import annotations

from typing import TypedDict

from change_review_orchestrator.domain.models import WorkflowState


class GraphState(TypedDict):
    """
    Top-level state type for the LangGraph StateGraph.

    A single key 'workflow' carries the full WorkflowState. This keeps
    the graph state schema minimal while still providing full domain
    typing through WorkflowState.

    LangGraph serialises this dict between node calls using its built-in
    checkpoint mechanism (in-memory by default; Postgres in Step 12).
    """

    workflow: WorkflowState
