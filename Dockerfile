# syntax=docker/dockerfile:1
# ── Stage 1: builder ──────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps only in builder stage
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir ".[prod]" --prefix=/install

# ── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="change-review-orchestrator"
LABEL org.opencontainers.image.description="Automated multi-agent change review pipeline for banking systems"
LABEL org.opencontainers.image.version="1.0.0"

WORKDIR /app

# Install runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Non-root user for security
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Expose API port
EXPOSE 8000

# Prometheus metrics port (optional)
EXPOSE 9090

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint
CMD ["uvicorn", "change_review_orchestrator.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--log-config", "/dev/null"]
