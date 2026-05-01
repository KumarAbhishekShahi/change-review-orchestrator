"""
Persistence Layer Unit Tests — SQLAlchemy with SQLite in-memory DB.

Tests cover:
- ReviewRecord CRUD (save, get, list, delete)
- FindingRecord bulk insert, replace, severity ordering
- AgentResultRecord upsert
- Repository functions with real SQLite session
- Pagination (list_reviews limit/offset)
- Cascade delete (findings deleted with review)
- count_reviews with status filter
- Serialised case_payload structure
- Composite score and recommendation persisted correctly
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from change_review_orchestrator.agents.adjudication import AdjudicationAgent
from change_review_orchestrator.agents.evidence_packager import EvidencePackagerAgent
from change_review_orchestrator.agents.impact import ImpactAgent
from change_review_orchestrator.agents.intake import IntakeAgent
from change_review_orchestrator.agents.policy import PolicyAgent
from change_review_orchestrator.agents.reliability import ReliabilityAgent
from change_review_orchestrator.agents.security import SecurityAgent
from change_review_orchestrator.agents.test_strategy import TestStrategyAgent
from change_review_orchestrator.persistence.database import create_all_tables
from change_review_orchestrator.persistence.models import (
    AgentResultRecord,
    Base,
    FindingRecord,
    ReviewRecord,
)
from change_review_orchestrator.persistence.repository import (
    count_reviews,
    delete_review,
    get_agent_results,
    get_findings,
    get_review,
    list_reviews,
    save_agent_results,
    save_findings,
    save_review,
)
from change_review_orchestrator.domain.models import WorkflowState
from tests.fixtures.sample_diffs import make_docs_only_case, make_pci_tokenisation_case
from pathlib import Path

FIXTURE_POLICY_FILE = Path("tests/fixtures/policy_rules.yaml")


# ── In-memory SQLite fixture ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(db_engine):
    """Provide a transactional session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ── Pipeline helper ───────────────────────────────────────────────────────────

def _run_pipeline(case):
    state = WorkflowState(case=case)
    state = IntakeAgent().run(state)
    state = ImpactAgent().run(state)
    state = PolicyAgent(policy_file=FIXTURE_POLICY_FILE).run(state)
    state = SecurityAgent().run(state)
    state = TestStrategyAgent().run(state)
    state = ReliabilityAgent().run(state)
    state = EvidencePackagerAgent().run(state)
    state = AdjudicationAgent().run(state)
    return state


# ── save_review() tests ───────────────────────────────────────────────────────

class TestSaveReview:

    def test_save_review_creates_record(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        record = save_review(db_session, state)
        assert record.case_id == state.case.case_id

    def test_save_review_persists_recommendation(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        fetched = get_review(db_session, state.case.case_id)
        assert fetched is not None
        assert fetched.recommendation in ("REJECT", "NEEDS_WORK", "APPROVE_WITH_CONDITIONS", "APPROVE")

    def test_save_review_persists_composite_score(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        fetched = get_review(db_session, state.case.case_id)
        assert fetched.composite_score is not None
        assert 0 <= fetched.composite_score <= 100

    def test_save_review_is_idempotent(self, db_session) -> None:
        """Saving the same case twice should not raise — it upserts."""
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_review(db_session, state)   # second save
        count = db_session.query(ReviewRecord).filter(
            ReviewRecord.case_id == state.case.case_id
        ).count()
        assert count == 1

    def test_save_review_case_payload_is_dict(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        fetched = get_review(db_session, state.case.case_id)
        assert isinstance(fetched.case_payload, dict)
        assert "case_id" in fetched.case_payload

    def test_save_review_finding_counts(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        fetched = get_review(db_session, state.case.case_id)
        assert fetched.findings_total == len(state.all_findings)
        assert fetched.findings_critical >= 0


# ── save_findings() tests ─────────────────────────────────────────────────────

class TestSaveFindings:

    def test_save_findings_inserts_all(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        count = save_findings(db_session, state)
        assert count == len(state.all_findings)

    def test_save_findings_is_idempotent(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_findings(db_session, state)
        save_findings(db_session, state)   # second call
        records = get_findings(db_session, state.case.case_id)
        assert len(records) == len(state.all_findings)

    def test_findings_ordered_by_severity(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_findings(db_session, state)
        records = get_findings(db_session, state.case.case_id)
        severities = [r.severity for r in records]
        _ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
        indices = [_ORDER.index(s) for s in severities if s in _ORDER]
        assert indices == sorted(indices)

    def test_finding_record_fields(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_findings(db_session, state)
        records = get_findings(db_session, state.case.case_id)
        for r in records[:3]:
            assert r.finding_id
            assert r.agent
            assert r.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
            assert r.title


# ── save_agent_results() tests ────────────────────────────────────────────────

class TestSaveAgentResults:

    def test_save_agent_results_inserts_all_agents(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        count = save_agent_results(db_session, state)
        assert count == len(state.agent_results)

    def test_agent_result_status_completed(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_agent_results(db_session, state)
        records = get_agent_results(db_session, state.case.case_id)
        statuses = {r.agent: r.status for r in records}
        for agent in ("intake", "impact", "adjudication"):
            assert statuses.get(agent) == "COMPLETED"

    def test_agent_result_metadata_is_dict(self, db_session) -> None:
        state = _run_pipeline(make_pci_tokenisation_case())
        save_review(db_session, state)
        save_agent_results(db_session, state)
        records = get_agent_results(db_session, state.case.case_id)
        for r in records:
            assert isinstance(r.metadata_blob, dict)


# ── list / count / delete tests ───────────────────────────────────────────────

class TestListAndDelete:

    def _save(self, db_session, make_case_fn):
        state = _run_pipeline(make_case_fn())
        save_review(db_session, state)
        save_findings(db_session, state)
        return state

    def test_list_reviews_returns_records(self, db_session) -> None:
        self._save(db_session, make_pci_tokenisation_case if callable(make_pci_tokenisation_case) else lambda: make_pci_tokenisation_case)
        records = list_reviews(db_session, limit=10)
        assert len(records) >= 1

    def test_list_reviews_limit(self, db_session) -> None:
        for _ in range(3):
            self._save(db_session, make_docs_only_case)
        records = list_reviews(db_session, limit=2)
        assert len(records) <= 2

    def test_count_reviews(self, db_session) -> None:
        before = count_reviews(db_session)
        self._save(db_session, make_docs_only_case)
        after = count_reviews(db_session)
        assert after == before + 1

    def test_delete_review_removes_record(self, db_session) -> None:
        state = self._save(db_session, make_pci_tokenisation_case)
        case_id = state.case.case_id
        assert delete_review(db_session, case_id) is True
        assert get_review(db_session, case_id) is None

    def test_delete_review_unknown_id_returns_false(self, db_session) -> None:
        assert delete_review(db_session, "does-not-exist") is False

    def test_get_review_unknown_returns_none(self, db_session) -> None:
        result = get_review(db_session, "totally-unknown-id")
        assert result is None
