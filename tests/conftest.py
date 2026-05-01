"""
Shared pytest fixtures for Change Review Orchestrator tests.

Fixtures defined here are automatically available to all test modules.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from change_review_orchestrator.config import Settings, get_settings
from change_review_orchestrator.main import create_app


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """
    Return a Settings instance pre-configured for tests.

    Clears lru_cache so production .env values do not bleed into the
    test environment.
    """
    get_settings.cache_clear()
    return Settings(
        app_env="test",
        app_log_level="DEBUG",
        postgres_db="change_review_test",
        enable_llm_agents=False,
        enable_human_in_the_loop=False,
    )


@pytest.fixture(scope="session")
def test_app(test_settings: Settings) -> TestClient:
    """
    Create a synchronous TestClient wrapping a fresh FastAPI app instance.
    """
    get_settings.cache_clear()
    application = create_app()
    with TestClient(application, raise_server_exceptions=True) as client:
        yield client
    get_settings.cache_clear()
