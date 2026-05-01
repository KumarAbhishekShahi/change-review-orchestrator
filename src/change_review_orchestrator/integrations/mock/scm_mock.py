"""
Mock SCM adapter for local development and testing.

Returns static data that mirrors the structure of the banking PR fixture.
Used by IntakeAgent when ENABLE_LLM_AGENTS=false and no real SCM token
is configured.
"""

from __future__ import annotations

from typing import Any

from change_review_orchestrator.integrations.base import SCMAdapter


class MockSCMAdapter(SCMAdapter):
    """
    In-memory SCM adapter returning fixture-based PR data.

    No network calls, no credentials required.
    """

    def get_pull_request(self, repo: str, pr_ref: str) -> dict[str, Any]:
        """Return mock PR metadata matching the banking fixture."""
        return {
            "source_ref": pr_ref,
            "repository": repo,
            "title": f"[MOCK] PR {pr_ref} from {repo}",
            "description": "Mock PR description — replace with real SCM adapter in production.",
            "author": "mock-author@bank.com",
            "reviewers": ["reviewer-a@bank.com"],
            "labels": ["mock"],
            "commit_sha": "0000000",
            "target_branch": "main",
        }

    def get_file_diff(self, repo: str, commit_sha: str, file_path: str) -> str:
        """Return a minimal mock diff string."""
        return (
            f"--- a/{file_path}\n"
            f"+++ b/{file_path}\n"
            "@@ -1,3 +1,5 @@\n"
            "+# mock addition\n"
            " existing line\n"
        )
