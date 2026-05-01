from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from change_review_orchestrator.api.routes.health import router as health_router
from change_review_orchestrator.api.routes.reviews import router as reviews_router
from change_review_orchestrator.logging_setup import configure_logging
configure_logging()

@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        from change_review_orchestrator.persistence.database import create_all_tables
        create_all_tables()
    except Exception:
        pass
    yield

app = FastAPI(title="Change Review Orchestrator", version="1.0.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(reviews_router)
