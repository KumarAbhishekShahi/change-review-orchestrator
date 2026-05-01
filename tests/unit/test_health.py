"""
Unit tests for the /health endpoint.

These tests run without any external dependencies (no DB, no Redis)
and must complete in milliseconds.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self, test_app: TestClient) -> None:
        """Health endpoint must return HTTP 200."""
        response = test_app.get("/health")
        assert response.status_code == 200

    def test_health_response_schema(self, test_app: TestClient) -> None:
        """Response must contain all required fields with correct types."""
        response = test_app.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert isinstance(body["version"], str)
        assert isinstance(body["environment"], str)
        assert isinstance(body["timestamp"], str)

    def test_health_environment_is_test(self, test_app: TestClient) -> None:
        """When APP_ENV=test the health response must reflect that value."""
        response = test_app.get("/health")
        body = response.json()
        assert body["environment"] == "test"

    def test_health_timestamp_is_iso8601(self, test_app: TestClient) -> None:
        """Timestamp must be a valid ISO-8601 datetime string."""
        import datetime
        response = test_app.get("/health")
        ts = response.json()["timestamp"]
        datetime.datetime.fromisoformat(ts)
