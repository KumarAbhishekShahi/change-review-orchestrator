"""
API Integration Tests — FastAPI route tests using TestClient.

Tests cover:
- GET /health and /ready return 200 with expected shape
- POST /api/v1/reviews/sync — full pipeline, valid response shape
- POST /api/v1/reviews — async endpoint, returns 202 + case_id
- GET /api/v1/reviews/{case_id} — status polling
- GET /api/v1/reviews/{case_id}/report — Markdown report download
- GET /api/v1/reviews/{case_id}/bundle — JSON audit bundle download
- GET /api/v1/reviews — list endpoint
- 404 on unknown case_id
- Request validation (missing required fields)
- PCI case returns REJECT recommendation
- Docs-only case returns APPROVE or APPROVE_WITH_CONDITIONS
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from change_review_orchestrator.main import app

client = TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _pci_payload() -> dict[str, Any]:
    return {
        "title": "PCI Tokenisation Service v2",
        "source_system": "github",
        "source_ref": "https://github.com/bank/payments/pull/42",
        "repository": "bank/payments",
        "branch": "feature/tokenisation-v2",
        "author": "dev@bank.com",
        "commit_sha": "abc1234",
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
            {"path": "infra/vault.tf",
             "lines_added": 40, "lines_removed": 5},
            {"path": "api/schema/payment_v2.yaml",
             "lines_added": 60, "lines_removed": 20, "is_breaking_change": True},
            {"path": "requirements.txt",
             "lines_added": 3, "lines_removed": 1},
        ],
    }


def _docs_payload() -> dict[str, Any]:
    return {
        "title": "Update API docs",
        "author": "writer@bank.com",
        "commit_sha": "def5678",
        "change_type": "DOCUMENTATION",
        "data_classification": "PUBLIC",
        "changed_files": [
            {"path": "docs/api_guide.md", "lines_added": 20, "lines_removed": 5},
            {"path": "README.md", "lines_added": 5, "lines_removed": 2},
        ],
    }


# ── Health endpoint tests ─────────────────────────────────────────────────────

class TestHealthEndpoints:

    def test_health_returns_200(self) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_shape(self) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert "agents_available" in data
        assert len(data["agents_available"]) == 9

    def test_ready_returns_200(self) -> None:
        resp = client.get("/ready")
        assert resp.status_code == 200

    def test_ready_response_has_llm_field(self) -> None:
        resp = client.get("/ready")
        data = resp.json()
        assert "llm_available" in data
        assert isinstance(data["llm_available"], bool)


# ── POST /sync tests ──────────────────────────────────────────────────────────

class TestSyncReviewEndpoint:

    def test_pci_case_returns_200(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        assert resp.status_code == 200

    def test_sync_response_has_case_id(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        data = resp.json()
        assert "case_id" in data
        assert len(data["case_id"]) > 8

    def test_sync_response_has_recommendation(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        data = resp.json()
        assert data["recommendation"] in ("APPROVE", "APPROVE_WITH_CONDITIONS",
                                           "NEEDS_WORK", "REJECT")

    def test_pci_case_recommendation_is_reject_or_needs_work(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        assert resp.json()["recommendation"] in ("REJECT", "NEEDS_WORK")

    def test_docs_case_recommendation_is_approve(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_docs_payload())
        assert resp.json()["recommendation"] in ("APPROVE", "APPROVE_WITH_CONDITIONS")

    def test_sync_response_finding_counts(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        counts = resp.json()["finding_counts"]
        assert counts["total"] > 0
        assert "critical" in counts and "high" in counts

    def test_sync_response_agent_results_present(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        agents = resp.json()["agent_results"]
        assert len(agents) == 9
        agent_names = [a["agent"] for a in agents]
        for expected in ("intake", "impact", "policy", "security",
                          "test_strategy", "reliability",
                          "evidence_packager", "adjudication", "llm_narrative"):
            assert expected in agent_names

    def test_sync_response_adjudication_block(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        adj = resp.json()["adjudication"]
        assert adj is not None
        assert "composite_score" in adj
        assert "required_actions" in adj
        assert isinstance(adj["required_actions"], list)

    def test_sync_response_deployment_readiness(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        dr = resp.json()["deployment_readiness"]
        assert dr is not None
        assert "deployment_strategy" in dr
        assert 0 <= dr["deployment_risk_score"] <= 100

    def test_sync_response_top_findings(self) -> None:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        findings = resp.json()["top_findings"]
        assert len(findings) > 0
        for f in findings:
            assert "finding_id" in f
            assert f["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")

    def test_sync_missing_title_returns_422(self) -> None:
        payload = _pci_payload()
        del payload["title"]
        resp = client.post("/api/v1/reviews/sync", json=payload)
        assert resp.status_code == 422

    def test_sync_empty_files_accepted(self) -> None:
        payload = {"title": "Empty PR", "changed_files": []}
        resp = client.post("/api/v1/reviews/sync", json=payload)
        assert resp.status_code == 200


# ── POST /reviews (async) tests ───────────────────────────────────────────────

class TestAsyncReviewEndpoint:

    def test_async_returns_202(self) -> None:
        resp = client.post("/api/v1/reviews", json=_pci_payload())
        assert resp.status_code == 202

    def test_async_response_has_status_url(self) -> None:
        resp = client.post("/api/v1/reviews", json=_pci_payload())
        data = resp.json()
        assert "status_url" in data
        assert data["case_id"] in data["status_url"]


# ── GET /reviews/{case_id} tests ──────────────────────────────────────────────

class TestGetReviewEndpoint:

    def _create_review(self) -> str:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        return resp.json()["case_id"]

    def test_get_review_returns_200(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}")
        assert resp.status_code == 200

    def test_get_review_has_correct_case_id(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}")
        assert resp.json()["case_id"] == case_id

    def test_get_review_unknown_id_returns_404(self) -> None:
        resp = client.get("/api/v1/reviews/nonexistent-case-id")
        assert resp.status_code == 404


# ── GET /reviews/{case_id}/report tests ──────────────────────────────────────

class TestReportEndpoint:

    def _create_review(self) -> str:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        return resp.json()["case_id"]

    def test_report_returns_200(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/report")
        assert resp.status_code == 200

    def test_report_contains_markdown_header(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/report")
        assert "# Change Review Report" in resp.text

    def test_report_contains_findings_section(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/report")
        assert "## Findings" in resp.text

    def test_report_unknown_id_returns_404(self) -> None:
        resp = client.get("/api/v1/reviews/unknown-xyz/report")
        assert resp.status_code == 404


# ── GET /reviews/{case_id}/bundle tests ──────────────────────────────────────

class TestBundleEndpoint:

    def _create_review(self) -> str:
        resp = client.post("/api/v1/reviews/sync", json=_pci_payload())
        return resp.json()["case_id"]

    def test_bundle_returns_200(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/bundle")
        assert resp.status_code == 200

    def test_bundle_is_valid_json(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/bundle")
        bundle = resp.json()
        assert "case_id" in bundle
        assert "findings" in bundle
        assert "agent_results" in bundle

    def test_bundle_content_type_is_json(self) -> None:
        case_id = self._create_review()
        resp = client.get(f"/api/v1/reviews/{case_id}/bundle")
        assert "application/json" in resp.headers["content-type"]


# ── GET /reviews (list) tests ─────────────────────────────────────────────────

class TestListReviewsEndpoint:

    def test_list_returns_200(self) -> None:
        resp = client.get("/api/v1/reviews")
        assert resp.status_code == 200

    def test_list_response_shape(self) -> None:
        resp = client.get("/api/v1/reviews")
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_list_includes_created_review(self) -> None:
        sync_resp = client.post("/api/v1/reviews/sync", json=_docs_payload())
        case_id = sync_resp.json()["case_id"]
        list_resp = client.get("/api/v1/reviews")
        ids = [item["case_id"] for item in list_resp.json()["items"]]
        assert case_id in ids
