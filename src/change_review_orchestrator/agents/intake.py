"""
Intake and Evidence Agent — Change Review Orchestrator

Responsibilities:
1. Validate the incoming ChangeCase for required fields (detect missing metadata)
2. Classify each changed file into an AssetCategory using deterministic path rules
3. Re-compute canonical totals (files, lines, breaking changes)
4. Build an evidence index — one EvidenceItem per meaningful artefact
5. Write the canonical case file to the artefact store

Design principle: deterministic path-based rules first; no LLM calls needed
for classification when file extensions and paths are unambiguous.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import (
    AssetCategory,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import (
    AgentResult,
    ChangedFile,
    EvidenceItem,
    Finding,
    WorkflowState,
)
from change_review_orchestrator.integrations.mock.artifact_store_mock import (
    LocalFilesystemArtifactStore,
)

logger = structlog.get_logger(__name__)

# ── Deterministic classification rules ───────────────────────────────────────
# Ordered by specificity: more specific patterns first.
# Each rule is (regex_pattern, AssetCategory).

_CLASSIFICATION_RULES: list[tuple[re.Pattern[str], AssetCategory]] = [
    # Secrets / credentials — must always be flagged first
    (re.compile(r"(secret|credential|\.pem|\.key|\.p12|\.pfx|id_rsa)", re.I), AssetCategory.SECRET_OR_CREDENTIAL),

    # CI/CD pipelines
    (re.compile(r"(\.github/workflows|\.gitlab-ci|Jenkinsfile|\.circleci|\.drone)", re.I), AssetCategory.CI_CD_PIPELINE),

    # Infrastructure as Code
    (re.compile(r"\.(tf|tfvars|hcl|bicep|cfn\.yaml|cfn\.json)$", re.I), AssetCategory.INFRASTRUCTURE_AS_CODE),
    (re.compile(r"(infra|terraform|helm|k8s|kubernetes|ansible|pulumi)/", re.I), AssetCategory.INFRASTRUCTURE_AS_CODE),

    # Database migrations
    (re.compile(r"(alembic/versions|migrations?|flyway|liquibase)/", re.I), AssetCategory.DATABASE_MIGRATION),
    (re.compile(r"_migration\.(py|sql)$", re.I), AssetCategory.DATABASE_MIGRATION),

    # API schemas
    (re.compile(r"(openapi|swagger|graphql|proto|schema)\.(ya?ml|json|proto)$", re.I), AssetCategory.API_SCHEMA),
    (re.compile(r"/schema(s)?/", re.I), AssetCategory.API_SCHEMA),

    # Dependency manifests
    (re.compile(r"(requirements.*\.txt|pyproject\.toml|setup\.py|setup\.cfg|"
                r"package\.json|pom\.xml|build\.gradle|Gemfile|Cargo\.toml)$", re.I), AssetCategory.DEPENDENCY_MANIFEST),

    # Configuration files
    (re.compile(r"\.(ya?ml|json|toml|ini|cfg|conf|env\.example|properties)$", re.I), AssetCategory.CONFIGURATION),
    (re.compile(r"(config|settings|conf)/", re.I), AssetCategory.CONFIGURATION),

    # Documentation
    (re.compile(r"\.(md|rst|txt|adoc)$", re.I), AssetCategory.DOCUMENTATION),
    (re.compile(r"(docs?|documentation)/", re.I), AssetCategory.DOCUMENTATION),

    # Tests
    (re.compile(r"(test_|_test\.|spec_|_spec\.|/tests?/|/spec/)", re.I), AssetCategory.TEST),

    # Source code (catch-all for known code extensions)
    (re.compile(r"\.(py|java|kt|scala|go|rs|ts|js|tsx|jsx|cs|cpp|c|h|rb|php|swift)$", re.I), AssetCategory.SOURCE_CODE),
]

# Required fields on a ChangeCase that must be present for a complete review
_REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("author",          "Change author is not identified"),
    ("jira_ticket",     "No linked JIRA ticket — traceability gap"),
    ("release_version", "No release version specified"),
    ("commit_sha",      "No commit SHA — cannot pin the exact code version"),
]


def classify_file(path: str) -> AssetCategory:
    """
    Determine the AssetCategory for a file based on its path.

    Uses ordered regex rules. Returns UNKNOWN if no rule matches.

    Args:
        path: Relative file path from repo root.

    Returns:
        The matched AssetCategory, or AssetCategory.UNKNOWN.
    """
    for pattern, category in _CLASSIFICATION_RULES:
        if pattern.search(path):
            logger.debug("file_classified", path=path, category=category.value)
            return category
    return AssetCategory.UNKNOWN


class IntakeAgent(BaseAgent):
    """
    Intake and Evidence Agent.

    First agent in the pipeline. Validates, classifies, and indexes the
    incoming change request. Produces findings for any missing required
    metadata and for any files classified as SECRET_OR_CREDENTIAL.
    """

    agent_name = "intake"

    def __init__(self) -> None:
        self._store = LocalFilesystemArtifactStore()

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        """
        Perform intake processing on the ChangeCase.

        Steps:
        1. Detect missing required metadata → INFO/MEDIUM findings
        2. Classify changed files by path → enrich ChangedFile.category
        3. Flag SECRET_OR_CREDENTIAL files → CRITICAL finding
        4. Build evidence index
        5. Persist canonical case JSON to artefact store
        """
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("intake_processing_case", title=case.title, files=len(case.changed_files))

        # ── Step 1: Detect missing required metadata ─────────────────────────
        missing: list[str] = []
        for field_name, description in _REQUIRED_FIELDS:
            value = getattr(case, field_name, None)
            if not value:
                missing.append(field_name)
                severity = Severity.MEDIUM if field_name in ("author", "commit_sha") else Severity.LOW
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.COMPLIANCE,
                    severity=severity,
                    title=f"Missing metadata: {field_name}",
                    description=description,
                    remediation_guidance=f"Provide the '{field_name}' field in the change request.",
                ))
                log.warning("missing_metadata_field", field=field_name)

        # Persist the list so downstream agents can check it
        result.metadata["missing_metadata_fields"] = missing

        # ── Step 2: Classify changed files ────────────────────────────────────
        classified_files: list[dict[str, object]] = []
        category_counts: dict[str, int] = {}

        for cf in case.changed_files:
            # Respect any pre-set category; only classify if still UNKNOWN
            if cf.category == AssetCategory.UNKNOWN:
                inferred_category = classify_file(cf.path)
            else:
                inferred_category = cf.category

            category_counts[inferred_category.value] = (
                category_counts.get(inferred_category.value, 0) + 1
            )
            classified_files.append({
                "path": cf.path,
                "category": inferred_category.value,
                "lines_added": cf.lines_added,
                "lines_removed": cf.lines_removed,
                "is_breaking_change": cf.is_breaking_change,
            })

            # ── Step 3: Flag credential files immediately ─────────────────────
            if inferred_category == AssetCategory.SECRET_OR_CREDENTIAL:
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.SECURITY,
                    severity=Severity.CRITICAL,
                    title="Potential credential or key file in PR",
                    description=(
                        f"File '{cf.path}' matches credential/secret patterns. "
                        "No secrets, private keys, or certificate files should appear in PRs."
                    ),
                    affected_assets=[cf.path],
                    remediation_guidance=(
                        "Remove the file from the PR. Use a secrets manager (Vault, AWS Secrets "
                        "Manager) and reference secrets via environment variables."
                    ),
                ))
                log.error("credential_file_detected", path=cf.path)

        result.metadata["classified_files"] = classified_files
        result.metadata["category_counts"] = category_counts
        log.info(
            "files_classified",
            total=len(classified_files),
            breakdown=category_counts,
            breaking_changes=case.has_breaking_changes,
        )

        # ── Step 4: Build evidence index ──────────────────────────────────────
        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Canonical Case File",
            content_summary=(
                f"Change request '{case.title}' ({case.source_ref}) from "
                f"{case.repository or 'unknown repo'} — "
                f"{case.total_files_changed} files, "
                f"{case.total_lines_added} lines added, "
                f"{case.total_lines_removed} lines removed."
            ),
        ))

        if case.has_breaking_changes:
            breaking = [f.path for f in case.changed_files if f.is_breaking_change]
            result.evidence_items.append(EvidenceItem(
                source_agent=self.agent_name,
                label="Breaking Changes Index",
                content_summary=f"Breaking changes detected in: {', '.join(breaking)}",
            ))
            # Raise a HIGH finding so downstream agents know to scrutinise these files
            result.findings.append(Finding(
                agent=self.agent_name,
                category=FindingCategory.IMPACT,
                severity=Severity.HIGH,
                title="Breaking changes detected in PR",
                description=(
                    f"The following files contain backwards-incompatible changes: "
                    f"{', '.join(breaking)}. Downstream consumers must be notified."
                ),
                affected_assets=breaking,
                remediation_guidance=(
                    "Confirm a migration guide exists. Notify all API consumers. "
                    "Consider a versioned API endpoint rather than an in-place change."
                ),
            ))

        # ── Step 5: Persist canonical case JSON ───────────────────────────────
        case_json = case.model_dump_json(indent=2)
        artifact_path = self._store.write(case.case_id, "canonical_case.json", case_json)

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Canonical Case JSON",
            content_summary="Full normalised ChangeCase persisted to artefact store.",
            artifact_path=artifact_path,
        ))

        result.summary = (
            f"Intake complete. {len(case.changed_files)} files classified across "
            f"{len(category_counts)} categories. "
            f"Missing metadata fields: {missing or 'none'}. "
            f"Breaking changes: {'yes' if case.has_breaking_changes else 'no'}. "
            f"Findings raised: {len(result.findings)}."
        )

        log.info(
            "intake_complete",
            findings=len(result.findings),
            evidence_items=len(result.evidence_items),
            missing_fields=missing,
        )
        return result
