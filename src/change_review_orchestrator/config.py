"""
Application configuration using Pydantic Settings.

All settings are read from environment variables (or a .env file loaded
by python-dotenv). Never hardcode secrets — add them to .env.example
and load them here.

Usage:
    from change_review_orchestrator.config import get_settings
    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for Change Review Orchestrator.

    All fields have sensible defaults for local development. Override via
    environment variables or a .env file in the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="development", description="Runtime environment name")
    app_log_level: str = Field(default="INFO", description="Logging level")
    app_host: str = Field(default="0.0.0.0", description="Bind host")
    app_port: int = Field(default=8000, ge=1, le=65535, description="Bind port")
    app_secret_key: str = Field(
        default="dev-insecure-key",
        description="Secret key — MUST be changed in production",
    )

    # PostgreSQL
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="change_review")
    postgres_user: str = Field(default="cro_user")
    postgres_password: str = Field(default="cro_secret")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # LLM
    llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible LLM endpoint (e.g. local Ollama)",
    )
    llm_model: str = Field(default="llama3", description="Model identifier")
    llm_api_key: str = Field(
        default="ollama",
        description="API key — 'ollama' is the dummy value for local Ollama",
    )

    # Artifact storage
    artifact_store_path: Path = Field(
        default=Path("./artifacts"),
        description="Root directory for local artifact storage",
    )

    # Feature flags
    enable_llm_agents: bool = Field(
        default=False,
        description="Toggle LLM-based reasoning in agents (false = deterministic rules only)",
    )
    enable_human_in_the_loop: bool = Field(
        default=False,
        description="Pause workflow at escalation checkpoints for human review",
    )

    @property
    def postgres_dsn(self) -> str:
        """Build a SQLAlchemy-compatible sync DSN (used by Alembic)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_async_dsn(self) -> str:
        """Build a SQLAlchemy-compatible async DSN (used by the app)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @field_validator("app_log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure only valid Python log level names are accepted."""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"app_log_level must be one of {allowed}, got '{v}'")
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Uses lru_cache so the .env file is parsed only once per process.
    In tests, call get_settings.cache_clear() to reset between test cases.
    """
    return Settings()


# Module-level singleton
settings = get_settings()
