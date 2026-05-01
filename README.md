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
│  2. ImpactAgent         — risk scoring, blast radius    │
│  3. PolicyAgent         — obligation matching, gaps     │
│  4. SecurityAgent       — SAST, threat hypotheses       │
│  5. TestStrategyAgent   — coverage gap analysis         │
│  6. ReliabilityAgent    — deployment risk, rollback     │
│  7. EvidencePackager    — Markdown + JSON artefacts     │
│  8. AdjudicationAgent   — composite score, recommendation│
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
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate      # Linux/Mac
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
cp .env.example .env             # edit DATABASE_URL + GEMINI_API_KEY (optional)

# 2. Run all tests (320 tests — no external services needed)
python -m pytest tests\ -v

# 3. Start the API server
python -m uvicorn change_review_orchestrator.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Interactive API docs
Start-Process "http://localhost:8000/docs"   # Windows PowerShell
# open http://localhost:8000/docs            # Mac/Linux

# 5. Run E2E smoke test
python scripts/smoke_test.py --base-url http://localhost:8000
```

---

## Windows Commands Reference

### Health & Status

```powershell
# Liveness check
Invoke-RestMethod http://localhost:8000/health

# Readiness + LLM availability
Invoke-RestMethod http://localhost:8000/ready

# Open interactive API docs
Start-Process "http://localhost:8000/docs"
```

---

## Sample Scenarios & Expected Outcomes

Seven ready-made payloads live in `sample_data/`. Each exercises a different
combination of agents and produces a predictable recommendation.

| File | Change Type | Data Class | Expected | Key Agents Triggered |
|------|-------------|------------|----------|----------------------|
| `pr_payload_banking.json` | FEATURE | RESTRICTED | **REJECT** | Policy (PCI-DSS), Security, Adjudication escalation |
| `hotfix_production.json` | HOTFIX | CONFIDENTIAL | **APPROVE** | Intake (expedited), Reliability (low risk) |
| `db_migration_aml.json` | DB_MIGRATION | HIGHLY_RESTRICTED | **REJECT** | Policy (AML/GDPR), Reliability (no rollback) |
| `security_patch_oauth.json` | SECURITY_PATCH | RESTRICTED | **APPROVE_WITH_CONDITIONS** | Security (CVE+), Impact (auth blast radius) |
| `infra_k8s_upgrade.json` | INFRASTRUCTURE | INTERNAL | **NEEDS_WORK** | Reliability (K8s risk), Impact (all services) |
| `dependency_upgrade.json` | DEPENDENCY_UPGRADE | INTERNAL | **NEEDS_WORK** | Security (dep scan), Impact (SecurityConfig) |
| `docs_only.json` | DOCUMENTATION | PUBLIC | **APPROVE** | All agents → zero findings |

### Submit Each Scenario

```powershell
# Scenario 1 — PCI Tokenisation Feature → REJECT
# Policy fires PCI-DSS rules; Security flags auth+vault changes;
# Adjudication: ESC-001 (CRITICAL finding) + ESC-002 (PCI gap)
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\pr_payload_banking.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 2 — Production Hotfix → APPROVE
# Intake: HOTFIX type = expedited path; 2 files only = minimal blast radius;
# TestStrategy: test added alongside fix = HIGH confidence
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\hotfix_production.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 3 — AML Database Migration → REJECT
# Policy: HIGHLY_RESTRICTED + DB_MIGRATION fires GDPR + AML obligations;
# Reliability: backfill script = rollback_viable=False; ESC-003 fires
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\db_migration_aml.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 4 — Security Patch CVE → APPROVE_WITH_CONDITIONS
# Security: CVE fix = positive signal; Impact: auth service = high blast radius;
# Policy: RESTRICTED + JWT change = token lifecycle obligations checked
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\security_patch_oauth.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 5 — Kubernetes Infrastructure Upgrade → NEEDS_WORK
# Reliability: K8s node replacement = CRITICAL deployment risk;
# Impact: INFRASTRUCTURE type = all services affected = max blast radius
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\infra_k8s_upgrade.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 6 — Spring Boot Dependency Upgrade → NEEDS_WORK
# Security: scans transitive deps for CVEs; Impact: SecurityConfig change = auth blast radius;
# TestStrategy: no security-specific tests for new filter chain = coverage gap
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\dependency_upgrade.json" |
  Select-Object recommendation, composite_score, finding_counts

# Scenario 7 — Documentation Only → APPROVE (fastest path)
# Intake: DOCUMENTATION + PUBLIC = zero risk tier;
# Impact: no source files = blast radius NONE; all agents produce zero findings
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\docs_only.json" |
  Select-Object recommendation, composite_score, finding_counts
```

### Batch Run All 7 Scenarios

```powershell
$scenarios = @(
  "pr_payload_banking", "hotfix_production", "db_migration_aml",
  "security_patch_oauth", "infra_k8s_upgrade", "dependency_upgrade", "docs_only"
)
$results = @()
foreach ($s in $scenarios) {
    $r = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
         -ContentType "application/json" -InFile "sample_data\$s.json"
    $results += [PSCustomObject]@{
        Scenario       = $s
        Recommendation = $r.recommendation
        Score          = $r.composite_score
        Findings       = $r.finding_counts.total
        Critical       = $r.finding_counts.critical
        High           = $r.finding_counts.high
    }
    Write-Host "$s -> $($r.recommendation)  score=$($r.composite_score)"
}
$results | Format-Table -AutoSize
```

---

## Inspect Agent Results in Detail

```powershell
# Submit and capture full response
$resp = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\db_migration_aml.json"

# ── All 9 agents at a glance ──
$resp.agent_results | Format-Table agent, status, findings_count, summary -Wrap

# ── Individual agent deep-dives ──

# Agent 1 — Intake: risk tier, change classification, metadata
$resp.agent_results | Where-Object { $_.agent -eq "intake" } | Format-List

# Agent 2 — Impact: blast radius, risk score, affected systems
$resp.agent_results | Where-Object { $_.agent -eq "impact" } | Format-List

# Agent 3 — Policy: compliance obligations fired, gaps detected
$resp.agent_results | Where-Object { $_.agent -eq "policy" } | Format-List

# Agent 4 — Security: SAST findings, threat hypotheses, vulnerable paths
$resp.agent_results | Where-Object { $_.agent -eq "security" } | Format-List

# Agent 5 — Test Strategy: coverage gaps, missing test types, confidence score
$resp.agent_results | Where-Object { $_.agent -eq "test_strategy" } | Format-List

# Agent 6 — Reliability: deployment risk, rollback viability, blast consumers
$resp.agent_results | Where-Object { $_.agent -eq "reliability" } | Format-List

# Agent 7 — Evidence Packager: report path, bundle path, artefact list
$resp.agent_results | Where-Object { $_.agent -eq "evidence_packager" } | Format-List

# Agent 8 — Adjudication: composite score, escalation rules fired, required actions
$resp.agent_results | Where-Object { $_.agent -eq "adjudication" } | Format-List
$resp.adjudication.required_actions
$resp.adjudication.triggered_escalations

# Agent 9 — LLM Narrative: AI-generated prose summary (requires GEMINI_API_KEY)
$resp.agent_results | Where-Object { $_.agent -eq "llm_narrative" } | Format-List

# ── Findings sorted by severity ──
$resp.top_findings | Format-Table severity, agent, title -Wrap

# ── Deployment readiness details ──
$resp.deployment_readiness | Format-List
```

---

## Download Artefacts

```powershell
# Capture case_id from any submission
$resp = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -InFile "sample_data\pr_payload_banking.json"
$caseId = $resp.case_id

# Download Markdown report
Invoke-RestMethod "http://localhost:8000/api/v1/reviews/$caseId/report" |
  Out-File -FilePath "review_report.md" -Encoding utf8
notepad review_report.md

# Download JSON audit bundle (all findings + all agent metadata)
Invoke-RestMethod "http://localhost:8000/api/v1/reviews/$caseId/bundle" |
  ConvertTo-Json -Depth 10 | Out-File "audit_bundle.json" -Encoding utf8

# List all submitted reviews
Invoke-RestMethod "http://localhost:8000/api/v1/reviews" |
  Select-Object -ExpandProperty items |
  Format-Table case_id, recommendation, composite_score, title -Wrap

# Fetch a specific review
Invoke-RestMethod "http://localhost:8000/api/v1/reviews/$caseId" |
  Select-Object recommendation, composite_score, status
```

---

## Inline Custom Payload (no JSON file needed)

```powershell
$body = @{
    title = "Quick test — config flag change"
    change_type = "CONFIGURATION"
    data_classification = "INTERNAL"
    author = "me@bank.com"
    changed_files = @(
        @{ path = "config/feature_flags.yaml"; lines_added = 5; lines_removed = 2 }
    )
} | ConvertTo-Json -Depth 3

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/reviews/sync" `
  -ContentType "application/json" -Body $body |
  Select-Object recommendation, composite_score, finding_counts
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

## Agent Pipeline — What Each Agent Does

| # | Agent | Input | Output | Key Logic |
|---|-------|-------|--------|-----------|
| 1 | **Intake** | ChangeCase | Risk tier, metadata | Classifies change type, data classification → risk tier 1–4 |
| 2 | **Impact** | IntakeResult | Blast radius score, affected systems | Counts file categories, breaking changes → 0–100 risk score |
| 3 | **Policy** | ChangeCase + YAML rules | Policy findings, obligation gaps | Matches change attributes against policy_rules.yaml |
| 4 | **Security** | Changed files | SAST findings, threat hypotheses | Path-based heuristics: auth/ vault/ migration/ infra/ → findings |
| 5 | **TestStrategy** | All findings so far | Coverage gaps, confidence score | Checks test-to-code ratio, test file presence → 0–100 confidence |
| 6 | **Reliability** | ChangeCase + findings | Deployment risk, rollback viability | Change type risk weights + blast radius → deployment strategy |
| 7 | **EvidencePackager** | Full WorkflowState | Markdown report, JSON bundle | Aggregates all agent output into artefact files on disk |
| 8 | **Adjudication** | All agent results | Composite score (0–100), recommendation | Weighted scoring + 5 escalation rules → APPROVE / REJECT |
| 9 | **LLMNarrative** | WorkflowState + report | AI prose summary | Calls Gemini 1.5 Flash; gracefully skips if no API key |

---

## Recommendations

| Value | Meaning | Trigger |
|-------|---------|---------|
| `APPROVE` | Safe to merge | Score ≥ 80, no escalations |
| `APPROVE_WITH_CONDITIONS` | Merge after advisory actions | Score 60–79 |
| `NEEDS_WORK` | Block — high issues present | Score 40–59 or HIGH escalation |
| `REJECT` | Must not merge | Score < 40 or CRITICAL escalation |

---

## Escalation Rules (hard overrides)

| Rule | Trigger | Effect |
|------|---------|--------|
| ESC-001 | Any CRITICAL finding | Forces REJECT |
| ESC-002 | PCI-DSS policy gap | Forces REJECT |
| ESC-003 | Rollback not viable | Forces NEEDS_WORK minimum |
| ESC-004 | Security posture CRITICAL | Forces REJECT |
| ESC-005 | Test confidence < 30 | Forces NEEDS_WORK minimum |

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

## Configuration

```bash
# .env — SQLite local dev (no setup needed)
DATABASE_URL=sqlite:///./change_review.db

# .env — PostgreSQL production
DATABASE_URL=postgresql://user:pass@localhost:5432/change_review

# Gemini LLM (optional — pipeline works fully without it)
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-1.5-flash

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=console      # console | json
```

---

## Project Structure

```
src/change_review_orchestrator/
├── agents/              # 9 agent modules + base
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
│   ├── schemas.py
│   ├── pipeline_runner.py
│   └── routes/
│       ├── health.py
│       └── reviews.py
├── domain/
│   ├── enums.py
│   └── models.py
├── integrations/
│   ├── mock/            # Local artifact store, JIRA stub, GitHub stub
│   └── real/            # Gemini client
├── persistence/
│   ├── models.py        # SQLAlchemy ORM (3 tables)
│   ├── database.py      # Engine + session factory
│   └── repository.py    # All DB read/write operations
└── utils/
    ├── metrics.py        # Prometheus (graceful no-op if not installed)
    └── prompt_templates.py

sample_data/
├── pr_payload_banking.json     # PCI Feature → REJECT
├── hotfix_production.json      # Hotfix → APPROVE
├── db_migration_aml.json       # AML DB Migration → REJECT
├── security_patch_oauth.json   # CVE Patch → APPROVE_WITH_CONDITIONS
├── infra_k8s_upgrade.json      # K8s Upgrade → NEEDS_WORK
├── dependency_upgrade.json     # Spring Boot Upgrade → NEEDS_WORK
└── docs_only.json              # Docs → APPROVE

tests/
├── unit/                 # 289 test methods (10 files)
├── integration/          # 31 test methods (API routes)
└── fixtures/             # Sample diffs + policy_rules.yaml

alembic/versions/
└── 0001_initial_schema.py

scripts/
├── run_local.py
└── smoke_test.py

infra/
└── prometheus.yml
```

---

## Windows Developer Commands

```powershell
# Run all tests
python -m pytest tests\ -v

# Run only unit tests
python -m pytest tests\unit\ -v

# Run only integration tests
python -m pytest tests\integration\ -v

# Run with coverage report
python -m pytest tests\ -v --cov=src --cov-report=html

# Start API server (auto-reload)
python -m uvicorn change_review_orchestrator.main:app --host 0.0.0.0 --port 8000 --reload

# Run Alembic migration
python -m alembic upgrade head

# Lint
python -m ruff check src tests

# Format
python -m ruff format src tests

# Full Docker stack
docker compose up --build

# With Prometheus + Grafana monitoring
docker compose --profile monitoring up --build

# E2E smoke test
python scripts/smoke_test.py --base-url http://localhost:8000
```

---

## Implementation Steps (all complete ✅)

| Step | Description |
|------|-------------|
| 1 | Project scaffold, pyproject.toml, CI skeleton, Makefile |
| 2 | Domain models: enums, ChangeCase, Finding, WorkflowState |
| 3 | Intake Agent — classification, risk tier, metadata extraction |
| 4 | Impact Agent — blast radius, risk score, concern detection |
| 5 | Policy Agent — YAML rule engine, obligation matching, gap detection |
| 6 | Security Agent — SAST heuristics, threat hypotheses |
| 7 | Mock integrations — JIRA, GitHub, Artifact Store |
| 8 | Test Strategy Agent — coverage gap analysis, confidence scoring |
| 9 | Reliability Agent — deployment risk, rollback assessment, blast radius |
| 10 | Evidence Packager + Adjudication Agent — artefacts + composite scoring |
| 11 | LLM Narrative Overlay — Gemini 1.5 Flash with graceful degradation |
| 12 | FastAPI layer — 8 endpoints, OpenAPI docs |
| 13 | Persistence layer — SQLAlchemy ORM, Alembic migration, repository pattern |
| 14 | Observability, Docker, GitHub Actions CI, E2E smoke test |

---

*Built with Python 3.11+, FastAPI, SQLAlchemy, structlog, Pydantic v2, Gemini 1.5 Flash.*
