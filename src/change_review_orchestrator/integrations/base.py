"""
Abstract adapter interfaces for external integrations.

Every external system (SCM, SAST, ticketing, vault) is accessed through
an abstract base class defined here. Mock implementations live in
integrations/mock/. Real implementations will live in integrations/real/.

This pattern ensures:
- Agents never import concrete integration classes directly
- Swapping mock → real requires zero agent code changes
- Integration logic is fully testable in isolation
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SCMAdapter(ABC):
    """
    Abstract interface for Source Control Management systems.

    Provides normalised PR/MR metadata regardless of whether the
    backing system is GitHub, GitLab, Bitbucket, or a local stub.
    """

    @abstractmethod
    def get_pull_request(self, repo: str, pr_ref: str) -> dict[str, Any]:
        """
        Fetch PR metadata and file diff summary.

        Args:
            repo:   Repository identifier, e.g. "org/payments-service"
            pr_ref: PR number or reference string

        Returns:
            Normalised dict with keys: title, description, author,
            changed_files (list), labels, reviewers, commit_sha.
        """
        ...

    @abstractmethod
    def get_file_diff(self, repo: str, commit_sha: str, file_path: str) -> str:
        """Return the unified diff string for a single file."""
        ...


class ArtifactStore(ABC):
    """
    Abstract interface for storing and retrieving review artefacts.

    In local dev this writes to the filesystem. In production it would
    wrap S3, GCS, or Azure Blob Storage.
    """

    @abstractmethod
    def write(self, case_id: str, filename: str, content: str) -> str:
        """
        Write content to the artefact store.

        Returns:
            The relative path under which the artefact was stored.
        """
        ...

    @abstractmethod
    def read(self, artefact_path: str) -> str:
        """Read and return content from the artefact store."""
        ...
