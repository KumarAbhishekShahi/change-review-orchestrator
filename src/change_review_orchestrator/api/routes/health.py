"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from change_review_orchestrator.api.schemas import HealthResponse
from change_review_orchestrator.integrations.real.gemini_client import GeminiClient

router = APIRouter(tags=["health"])

_AGENTS = [
    "intake", "impact", "policy", "security", "test_strategy",
    "reliability", "evidence_packager", "adjudication", "llm_narrative",
]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — returns 200 if the service is running."""
    client = GeminiClient()
    return HealthResponse(
        status="ok",
        agents_available=_AGENTS,
        llm_available=client.available,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    """Readiness probe — checks agent availability."""
    client = GeminiClient()
    return HealthResponse(
        status="ready",
        agents_available=_AGENTS,
        llm_available=client.available,
    )
