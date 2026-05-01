# Change Review Orchestrator

> **Automated multi-agent change review pipeline for banking systems.**
> Orchestrates 9 specialised agents to assess every pull request against
> policy compliance, security posture, test coverage, and deployment
> reliability — producing a structured recommendation and audit artefacts.

---

## Architecture Overview

```
PR / Webhook
     │
     ▼
┌──────────────┐
│  FastAPI API │  POST /api/v1/reviews/sync
└──────┬───────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│                   Pipeline Runner                        │
│                                                         │
│  1. IntakeAgent         — classification, metadata      │
│  2. ImpactAgent         — risk scoring, concerns        │
│  3. PolicyAgent         — obligation matching, gaps     │
│  4. SecurityAgent       — SAST, threat hypotheses       │
│  5. TestStrategyAgent   — coverage gap analysis         │
│  6. ReliabilityAgent    — deployment risk, blast radius │
│  7. EvidencePackager    — Markdown + JSON artefacts     │
│  8. AdjudicationAgent   — composite score, REJECT/APPROVE│
│  9. LLMNarrativeAgent   — Gemini prose overlay          │
└────────────┬────────────────────────────────────────────┘
             │
     ┌───────┴───────┐
     │               │
     ▼               ▼
review_report.md   audit_bundle.json
(Markdown)         (Machine-readable)
     │               │
     └───────┬───────┘
             ▼
      PostgreSQL / SQLite
      (reviews, findings, agent_results)
```

---

## Quick Start

```bash
# 1. Clone and set up
git clone https://github.com/your-org/change-review-orchestrator
cd change-review-orchestrator
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # edit DATABASE_URL + GEMINI_API_KEY

# 2. Run tests (320 tests — no external services needed)
make test

# 3. Start the API server
make dev                    # uvicorn --reload on :8000

# 4. Interactive API docs
open http://localhost:8000/docs

# 5. Submit a review
curl -X POST http://localhost:8000/api/v1/reviews/sync \
  -H "Content-Type: application/json" \
  -d @sample_data/pr_payload_banking.json | jq .recommendation

# 6. Run E2E smoke test
python scripts/smoke_test.py --base-url http://localhost:8000
```

---

## Docker

```bash
# Full stack (API + PostgreSQL)
docker compose up --build

# With monitoring (Prometheus + Grafana)
docker compose --profile monitoring up --build

# Ports
#   8000  — FastAPI API
#   5432  — PostgreSQL
#   9091  — Prometheus
#   3000  — Grafana (admin/admin)
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Liveness probe |
| `GET`  | `/ready` | Readiness probe + LLM availability |
| `GET`  | `/docs` | OpenAPI UI (Swagger) |
| `POST` | `/api/v1/reviews/sync` | Run full pipeline, return result |
| `POST` | `/api/v1/reviews` | Accept review (202), poll for result |
| `GET`  | `/api/v1/reviews` | List all reviews |
| `GET`  | `/api/v1/reviews/{case_id}` | Get full review result |
| `GET`  | `/api/v1/reviews/{case_id}/report` | Download Markdown report |
| `GET`  | `/api/v1/reviews/{case_id}/bundle` | Download JSON audit bundle |

---

## Recommendations

| Value | Meaning | Trigger |
|-------|---------|---------|
| `APPROVE` | Safe to merge | Score ≥ 80, no escalations |
| `APPROVE_WITH_CONDITIONS` | Merge after advisory actions | Score 60–79 |
| `NEEDS_WORK` | Block — high issues present | Score 40–59 or HIGH escalation |
| `REJECT` | Must not merge | Score < 40 or CRITICAL escalation |

---

## Composite Scoring (0–100, lower = more risk)

| Dimension | Max Deduction | Logic |
|-----------|--------------|-------|
| Finding severity | 35 | CRITICAL=−18, HIGH=−8, MEDIUM=−3 per finding |
| Policy gaps | 20 | CRITICAL gap=−10, HIGH=−6 |
| Security posture | 20 | CRITICAL posture=−20 |
| Test confidence | 15 | `(1 − confidence/100) × 15` |
| Deployment risk | 10 | `risk_score/100 × 10`; no rollback adds −5 |

---

## Escalation Rules (hard overrides)

| Rule | Trigger | Minimum Recommendation |
|------|---------|----------------------|
| ESC-001 | Any CRITICAL finding | REJECT |
| ESC-002 | PCI-DSS policy gap | REJECT |
| ESC-003 | Rollback not viable | NEEDS_WORK |
| ESC-004 | Security posture CRITICAL | REJECT |
| ESC-005 | Test confidence < 30 | NEEDS_WORK |

---

## Configuration

All settings via environment variables (see `.env.example`):

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/change_review

# Gemini LLM (optional — pipeline works without it)
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-1.5-flash
GEMINI_MAX_TOKENS=1024

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json          # json | console
```

---

## Project Structure

```
src/change_review_orchestrator/
├── agents/              # 10 agent modules + base
│   ├── base.py
│   ├── intake.py
│   ├── impact.py
│   ├── policy.py
│   ├── security.py
│   ├── test_strategy.py
│   ├── reliability.py
│   ├── evidence_packager.py
│   ├── adjudication.py
│   └── llm_narrative.py
├── api/
│   ├── schemas.py       # Request / Response models
│   ├── pipeline_runner.py
│   └── routes/
│       ├── health.py
│       └── reviews.py
├── domain/
│   ├── enums.py         # ChangeType, Severity, FindingCategory …
│   └── models.py        # ChangeCase, Finding, WorkflowState …
├── integrations/
│   ├── mock/            # Local stubs (artifact store, JIRA, GitHub)
│   └── real/            # Gemini client
├── persistence/
│   ├── models.py        # SQLAlchemy ORM
│   ├── database.py      # Engine + session factory
│   └── repository.py    # All DB operations
└── utils/
    ├── metrics.py        # Prometheus (graceful degradation)
    └── prompt_templates.py

tests/
├── unit/                 # 289 test methods (10 files)
├── integration/          # 31 test methods (API routes)
└── fixtures/             # Sample diffs + policy YAML

alembic/versions/
└── 0001_initial_schema.py

scripts/
├── run_local.py
└── smoke_test.py

infra/
└── prometheus.yml
```

---

## Development Commands

```bash
make help          # List all commands
make dev           # Start API with auto-reload
make test          # Run all 320 tests
make test-unit     # Unit tests only
make test-int      # Integration tests only
make lint          # ruff check + format
make smoke         # Run E2E smoke test against local server
make migrate       # alembic upgrade head
make docker-build  # Build Docker image
make docker-up     # docker compose up --build
```

---

## Implementation Steps (all complete)

| Step | Description | Status |
|------|-------------|--------|
| 1  | Project scaffold, tooling, CI skeleton | ✅ |
| 2  | Domain models: enums, ChangeCase, Finding, WorkflowState | ✅ |
| 3  | Intake Agent | ✅ |
| 4  | Impact Agent | ✅ |
| 5  | Policy Agent + YAML rule engine | ✅ |
| 6  | Security Agent (SAST + threat) | ✅ |
| 7  | Mock integrations (JIRA, GitHub, Artifact Store) | ✅ |
| 8  | Test Strategy Agent | ✅ |
| 9  | Reliability Agent | ✅ |
| 10 | Evidence Packager + Adjudication Agent | ✅ |
| 11 | LLM Narrative Overlay (Gemini) | ✅ |
| 12 | FastAPI layer (8 endpoints) | ✅ |
| 13 | Persistence layer (SQLAlchemy + Alembic) | ✅ |
| 14 | Observability, Docker, CI, smoke test, README | ✅ |

---

*Built with Python 3.12, FastAPI, SQLAlchemy, structlog, Pydantic v2, Gemini 1.5 Flash.*
