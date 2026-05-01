#!/usr/bin/env python3
"""
Local mock run script for Change Review Orchestrator.

Loads the sample banking PR fixture, builds the LangGraph workflow,
and runs the full pipeline end-to-end using stub agents.

Usage:
    python scripts/run_local.py
    python scripts/run_local.py --fixture tests/fixtures/sample_pr_payload.json
    python scripts/run_local.py --pretty   # pretty-print final state JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure src/ is on the path when running as a script (not installed)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from change_review_orchestrator.config import get_settings
from change_review_orchestrator.domain.models import ChangeCase, WorkflowState
from change_review_orchestrator.domain.serializers import from_json, workflow_state_to_json
from change_review_orchestrator.logging_setup import configure_logging
from change_review_orchestrator.workflow.graph import build_graph
from change_review_orchestrator.workflow.state import GraphState


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CRO pipeline locally with stub agents")
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/sample_pr_payload.json",
        help="Path to a JSON file matching the ChangeCase schema",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the final workflow state JSON",
    )
    args = parser.parse_args()

    # Initialise logging
    settings = get_settings()
    configure_logging(settings)

    print("\n" + "="*60)
    print("  Change Review Orchestrator — Local Mock Run")
    print("="*60)

    # Load the fixture
    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"ERROR: Fixture not found: {fixture_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[1/4] Loading fixture: {fixture_path}")
    case: ChangeCase = from_json(fixture_path.read_bytes(), ChangeCase)
    print(f"      Case ID  : {case.case_id}")
    print(f"      Title    : {case.title}")
    print(f"      Repo     : {case.repository}")
    print(f"      Files    : {case.total_files_changed}")
    print(f"      Breaking : {case.has_breaking_changes}")

    # Build initial workflow state
    print("\n[2/4] Building initial WorkflowState...")
    initial_state = WorkflowState(case=case)
    graph_input: GraphState = {"workflow": initial_state}

    # Compile and run the graph
    print("\n[3/4] Compiling and running LangGraph pipeline...")
    graph = build_graph()
    final_state: GraphState = graph.invoke(graph_input)

    wf = final_state["workflow"]

    print("\n[4/4] Pipeline complete!")
    print(f"      Status          : {wf.status.value}")
    print(f"      Recommendation  : {wf.final_recommendation.value if wf.final_recommendation else '(stub — not yet set)'}")
    print(f"      Total Findings  : {len(wf.all_findings)}")
    print(f"      Total Evidence  : {len(wf.all_evidence)}")
    print(f"      Agents run      : {list(wf.agent_results.keys())}")
    print(f"      Errors          : {wf.error_log or 'none'}")

    if args.pretty:
        print("\n--- Final Workflow State (JSON) ---")
        print(workflow_state_to_json(wf, pretty=True))

    print("\n" + "="*60)
    print("  Run complete. All agents executed as stubs.")
    print("  Implement agent logic in Steps 4-10.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
