"""
Database engine and session factory.

Supports two backends:
- PostgreSQL (production)  via DATABASE_URL env var
- SQLite in-memory         (testing / local dev fallback)

Session is a context-managed synchronous session.
For async FastAPI endpoints use get_db() as a dependency.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import structlog
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from change_review_orchestrator.config import get_settings`nsettings = get_settings()
from change_review_orchestrator.persistence.models import Base

logger = structlog.get_logger(__name__)

_engine = None
_SessionLocal = None


def get_engine():
    """Return (creating if needed) the singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        db_url = settings.database_url or "sqlite:///./change_review.db"
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        _engine = create_engine(
            db_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info("db_engine_created", url=db_url.split("@")[-1])  # hide credentials
    return _engine


def get_session_factory() -> sessionmaker:
    """Return (creating if needed) the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionLocal


def create_all_tables() -> None:
    """Create all tables (idempotent). Called at startup."""
    Base.metadata.create_all(bind=get_engine())
    logger.info("db_tables_created")


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    Context-managed database session.

    Usage:
        with get_db() as db:
            db.add(record)
            db.commit()
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

