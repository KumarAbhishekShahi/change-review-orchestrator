.DEFAULT_GOAL := help
PYTHON        := python3.12
VENV          := .venv
PIP           := $(VENV)/bin/pip
SRC           := src
PYTEST        := $(VENV)/bin/pytest
RUFF          := $(VENV)/bin/ruff
MYPY          := $(VENV)/bin/mypy
UVICORN       := $(VENV)/bin/uvicorn

.PHONY: help venv install install-dev lint format typecheck test test-cov \
        run infra-up infra-down migrate clean pre-commit-install

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

venv:           ## Create virtual environment
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install:        ## Install production dependencies
	$(PIP) install -e .

install-dev:    ## Install all dependencies including dev tools
	$(PIP) install -e ".[dev]"

lint:           ## Run ruff linter
	$(RUFF) check $(SRC) tests

format:         ## Auto-format code with ruff
	$(RUFF) format $(SRC) tests

typecheck:      ## Run mypy type checks
	$(MYPY) $(SRC)

test:           ## Run all tests
	$(PYTEST) -v

test-cov:       ## Run tests with coverage report
	$(PYTEST) -v --cov=$(SRC) --cov-report=html

run:            ## Run the FastAPI development server
	$(UVICORN) change_review_orchestrator.main:app \
	  --host $${APP_HOST:-0.0.0.0} \
	  --port $${APP_PORT:-8000} \
	  --reload \
	  --log-level info

infra-up:       ## Start local infrastructure (Postgres, Redis)
	docker compose up -d

infra-down:     ## Stop local infrastructure
	docker compose down

migrate:        ## Run Alembic DB migrations
	$(VENV)/bin/alembic upgrade head

pre-commit-install: ## Install pre-commit hooks
	$(VENV)/bin/pre-commit install

clean:          ## Remove build artefacts and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist build .coverage htmlcov .mypy_cache .ruff_cache .pytest_cache
