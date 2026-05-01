"""
FastAPI Application entry point — Change Review Orchestrator
"""

from __future__ import annotations

from fastapi import FastAPI

from change_review_orchestrator.api.routes.health import router as health_router
from change_review_orchestrator.api.routes.reviews import router as reviews_router
from change_review_orchestrator.logging_setup import configure_logging
from change_review_orchestrator.persistence.database import create_all_tables

configure_logging()
create_all_tables()

app = FastAPI(
    title="Change Review Orchestrator",
    description=(
        "Automated multi-agent change review pipeline for banking systems. "
        "Provides policy compliance, security analysis, test strategy, "
        "reliability assessment, and AI-generated review reports."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(health_router)
app.include_router(reviews_router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "change-review-orchestrator",
        "version": "1.0.0",
        "docs": "/docs",
    }
