"""
Sample diff data and ChangeCase builders for Impact Agent tests.

Kept in a separate module so multiple test files can reuse realistic
banking-domain test inputs without copy-paste.
"""

from __future__ import annotations

from change_review_orchestrator.domain.enums import AssetCategory
from change_review_orchestrator.domain.models import ChangeCase, ChangedFile


def make_pci_tokenisation_case() -> ChangeCase:
    """Banking PR: PCI tokenisation feature with diverse high-risk files."""
    return ChangeCase(
        source_system="github",
        source_ref="PR-4821",
        repository="banking-org/payments-service",
        branch="feature/PAY-1234-pci-tokenisation",
        commit_sha="a3f9e2c",
        title="PAY-1234: Add PCI tokenisation layer for card data",
        author="jane.doe@bank.com",
        jira_ticket="PAY-1234",
        release_version="2.14.0",
        changed_files=[
            ChangedFile(
                path="src/payments/tokenisation/service.py",
                category=AssetCategory.SOURCE_CODE,
                lines_added=312, lines_removed=14,
            ),
            ChangedFile(
                path="src/payments/schema/payment.py",
                category=AssetCategory.API_SCHEMA,
                lines_added=28, lines_removed=45,
                is_breaking_change=True,
                breaking_change_reason="Field pan removed",
            ),
            ChangedFile(
                path="alembic/versions/0042_add_token_id_column.py",
                category=AssetCategory.DATABASE_MIGRATION,
                lines_added=67, lines_removed=0,
            ),
            ChangedFile(
                path="infra/vault/tokenisation-policy.hcl",
                category=AssetCategory.INFRASTRUCTURE_AS_CODE,
                lines_added=89, lines_removed=0,
            ),
            ChangedFile(
                path="tests/unit/payments/test_tokenisation_service.py",
                category=AssetCategory.TEST,
                lines_added=145, lines_removed=0,
            ),
            ChangedFile(
                path="requirements.txt",
                category=AssetCategory.DEPENDENCY_MANIFEST,
                lines_added=3, lines_removed=1,
            ),
        ],
    )


def make_auth_refactor_case() -> ChangeCase:
    """High-risk auth refactor touching JWT and session management."""
    return ChangeCase(
        title="Refactor JWT session management",
        author="dev@bank.com",
        commit_sha="b9f1a3c",
        changed_files=[
            ChangedFile(
                path="src/auth/jwt_handler.py",
                category=AssetCategory.SOURCE_CODE,
                lines_added=180, lines_removed=120,
            ),
            ChangedFile(
                path="src/auth/session_store.py",
                category=AssetCategory.SOURCE_CODE,
                lines_added=95, lines_removed=80,
            ),
            ChangedFile(
                path="tests/unit/auth/test_jwt.py",
                category=AssetCategory.TEST,
                lines_added=60, lines_removed=20,
            ),
        ],
    )


def make_docs_only_case() -> ChangeCase:
    """Low-risk docs-only change — should score minimal impact."""
    return ChangeCase(
        title="Update README and API docs",
        author="writer@bank.com",
        commit_sha="c1d2e3f",
        changed_files=[
            ChangedFile(
                path="README.md",
                category=AssetCategory.DOCUMENTATION,
                lines_added=15, lines_removed=5,
            ),
            ChangedFile(
                path="docs/api_guide.md",
                category=AssetCategory.DOCUMENTATION,
                lines_added=30, lines_removed=10,
            ),
        ],
    )


def make_pipeline_change_case() -> ChangeCase:
    """CI/CD pipeline change — medium risk."""
    return ChangeCase(
        title="Update CI pipeline to add SAST scan",
        author="platform@bank.com",
        commit_sha="d4e5f6a",
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                category=AssetCategory.CI_CD_PIPELINE,
                lines_added=45, lines_removed=10,
            ),
        ],
    )
