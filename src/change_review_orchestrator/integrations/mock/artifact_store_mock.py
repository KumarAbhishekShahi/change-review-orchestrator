"""
Local filesystem artefact store.

Writes artefacts under ARTIFACT_STORE_PATH/<case_id>/<filename>.
This is the default store for local development; swap for S3/GCS in prod.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from change_review_orchestrator.config import get_settings
from change_review_orchestrator.integrations.base import ArtifactStore

logger = structlog.get_logger(__name__)


class LocalFilesystemArtifactStore(ArtifactStore):
    """
    Stores artefacts as plain files on the local filesystem.

    Directory structure:
        {ARTIFACT_STORE_PATH}/
            {case_id}/
                {filename}
    """

    def __init__(self) -> None:
        self._root = get_settings().artifact_store_path

    def write(self, case_id: str, filename: str, content: str) -> str:
        """
        Write content to disk and return the relative artefact path.

        Args:
            case_id:  The change review case UUID.
            filename: Target filename, e.g. "report.md" or "audit_bundle.json".
            content:  UTF-8 string content to write.

        Returns:
            Relative path string: "<case_id>/<filename>"
        """
        case_dir = self._root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        target = case_dir / filename
        target.write_text(content, encoding="utf-8")

        rel_path = f"{case_id}/{filename}"
        logger.info("artifact_written", path=rel_path, size_bytes=len(content))
        return rel_path

    def read(self, artefact_path: str) -> str:
        """Read and return artefact content from disk."""
        full_path = self._root / artefact_path
        if not full_path.exists():
            raise FileNotFoundError(f"Artefact not found: {full_path}")
        return full_path.read_text(encoding="utf-8")
