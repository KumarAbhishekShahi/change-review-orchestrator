from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from change_review_orchestrator.main import app

@pytest.fixture(scope="session")
def test_client():
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
