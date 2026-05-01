#!/usr/bin/env python3
"""
End-to-End Smoke Test — Change Review Orchestrator

Submits two review cases to the live API and validates responses:
  1. PCI Tokenisation case  → expect REJECT or NEEDS_WORK
  2. Docs-only case         → expect APPROVE or APPROVE_WITH_CONDITIONS

Usage:
    python scripts/smoke_test.py --base-url http://localhost:8000
    python scripts/smoke_test.py --base-url http://localhost:8765
"""

from __future__ import annotations

import argparse
import json
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


_PCI_PAYLOAD = {
    "title": "Smoke Test — PCI Tokenisation Service v2",
    "source_system": "github",
    "repository": "bank/payments",
    "branch": "feature/tokenisation-v2",
    "author": "smoke-test@bank.com",
    "change_type": "FEATURE",
    "data_classification": "RESTRICTED",
    "has_breaking_changes": True,
    "changed_files": [
        {"path": "src/payments/tokenisation_service.py",
         "lines_added": 120, "lines_removed": 45},
        {"path": "src/auth/jwt_handler.py",
         "lines_added": 30, "lines_removed": 10},
        {"path": "db/migrations/0042_add_token_table.sql",
         "lines_added": 25, "lines_removed": 0, "is_breaking_change": True},
        {"path": "infra/vault.tf", "lines_added": 40, "lines_removed": 5},
        {"path": "api/schema/payment_v2.yaml",
         "lines_added": 60, "lines_removed": 20, "is_breaking_change": True},
    ],
}

_DOCS_PAYLOAD = {
    "title": "Smoke Test — Update API docs",
    "author": "writer@bank.com",
    "change_type": "DOCUMENTATION",
    "data_classification": "PUBLIC",
    "changed_files": [
        {"path": "docs/api_guide.md", "lines_added": 20, "lines_removed": 5},
        {"path": "README.md", "lines_added": 5, "lines_removed": 2},
    ],
}


def check(condition: bool, label: str) -> None:
    status = "✅  PASS" if condition else "❌  FAIL"
    print(f"  {status}  {label}")
    if not condition:
        sys.exit(1)


def run_smoke_test(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    print(f"\n🔍 Smoke Test — {base_url}\n")

    # ── Health ──────────────────────────────────────────────────────────
    print("1. Health check")
    resp = requests.get(f"{base_url}/health", timeout=10)
    check(resp.status_code == 200, f"GET /health → 200 (got {resp.status_code})")
    data = resp.json()
    check(data.get("status") == "ok", "status == ok")
    check(len(data.get("agents_available", [])) == 9, "9 agents available")

    # ── PCI case ─────────────────────────────────────────────────────────
    print("\n2. PCI Tokenisation case → expect REJECT or NEEDS_WORK")
    start = time.perf_counter()
    resp = requests.post(
        f"{base_url}/api/v1/reviews/sync",
        json=_PCI_PAYLOAD,
        timeout=120,
    )
    elapsed = time.perf_counter() - start
    check(resp.status_code == 200, f"POST /sync → 200 (got {resp.status_code})")
    data = resp.json()
    check("case_id" in data, "case_id present")
    check(data.get("recommendation") in ("REJECT", "NEEDS_WORK"),
          f"recommendation in (REJECT, NEEDS_WORK) — got {data.get('recommendation')}")
    check(data["finding_counts"]["total"] > 0,
          f"findings > 0 — got {data['finding_counts']['total']}")
    adj = data.get("adjudication", {})
    check(len(adj.get("required_actions", [])) > 0, "required_actions present")
    check(0 <= (data.get("composite_score") or 0) <= 100,
          f"composite_score in range — got {data.get('composite_score')}")
    check(data.get("report_available") is True, "report_available == True")
    pci_case_id = data["case_id"]
    print(f"     case_id: {pci_case_id}  ({elapsed:.1f}s)")

    # ── Docs case ─────────────────────────────────────────────────────────
    print("\n3. Docs-only case → expect APPROVE or APPROVE_WITH_CONDITIONS")
    resp = requests.post(
        f"{base_url}/api/v1/reviews/sync",
        json=_DOCS_PAYLOAD,
        timeout=60,
    )
    check(resp.status_code == 200, f"POST /sync → 200")
    data = resp.json()
    check(data.get("recommendation") in ("APPROVE", "APPROVE_WITH_CONDITIONS"),
          f"recommendation in (APPROVE, APPROVE_WITH_CONDITIONS) — got {data.get('recommendation')}")

    # ── Report download ────────────────────────────────────────────────────
    print("\n4. Markdown report download")
    resp = requests.get(f"{base_url}/api/v1/reviews/{pci_case_id}/report", timeout=10)
    check(resp.status_code == 200, "GET /report → 200")
    check("# Change Review Report" in resp.text, "Report contains markdown header")

    # ── JSON bundle ────────────────────────────────────────────────────────
    print("\n5. JSON audit bundle download")
    resp = requests.get(f"{base_url}/api/v1/reviews/{pci_case_id}/bundle", timeout=10)
    check(resp.status_code == 200, "GET /bundle → 200")
    bundle = resp.json()
    check("findings" in bundle, "bundle contains findings key")
    check(bundle.get("case_id") == pci_case_id, "bundle case_id matches")

    # ── List endpoint ──────────────────────────────────────────────────────
    print("\n6. List reviews")
    resp = requests.get(f"{base_url}/api/v1/reviews", timeout=10)
    check(resp.status_code == 200, "GET /reviews → 200")
    check(resp.json()["total"] >= 2, "total >= 2 after two submissions")

    print("\n✅  All smoke tests passed.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Change Review Orchestrator Smoke Test")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Base URL of the API server")
    args = parser.parse_args()
    run_smoke_test(args.base_url)
