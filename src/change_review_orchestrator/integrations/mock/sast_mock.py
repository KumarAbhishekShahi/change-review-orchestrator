"""
Mock SAST (Static Application Security Testing) scanner adapter.

Returns deterministic findings keyed on file path patterns so the
Security agent can exercise its full integration path without a real
scanner. In production, swap this for a Semgrep, Checkmarx, or
Snyk adapter in integrations/real/.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Pre-canned findings by path keyword -> list of mock scan results
_MOCK_SAST_FINDINGS: list[dict[str, Any]] = [
    {
        "rule_id": "SAST-001",
        "title": "Hardcoded credential detected",
        "severity": "CRITICAL",
        "path_pattern": r"(secret|password|credential|api.?key)",
        "cwe": "CWE-798",
        "description": "A string literal matching credential patterns was detected.",
        "remediation": "Use environment variables or a secrets manager instead.",
    },
    {
        "rule_id": "SAST-002",
        "title": "SQL injection risk — string concatenation in query",
        "severity": "CRITICAL",
        "path_pattern": r"(repository|dao|store|db|database|query|sql)",
        "cwe": "CWE-89",
        "description": "String concatenation used in a database query — potential SQL injection.",
        "remediation": "Use parameterised queries or an ORM.",
    },
    {
        "rule_id": "SAST-003",
        "title": "Insecure direct object reference",
        "severity": "HIGH",
        "path_pattern": r"(controller|handler|endpoint|route|view|api)",
        "cwe": "CWE-639",
        "description": "URL parameter used directly to fetch resource without authorisation check.",
        "remediation": "Add authorisation check before retrieving resource by user-supplied ID.",
    },
    {
        "rule_id": "SAST-004",
        "title": "JWT algorithm confusion — 'none' algorithm accepted",
        "severity": "CRITICAL",
        "path_pattern": r"(jwt|token|auth)",
        "cwe": "CWE-347",
        "description": "JWT validation may accept the 'none' algorithm, bypassing signature check.",
        "remediation": "Explicitly reject 'none' algorithm and whitelist only HS256 or RS256.",
    },
    {
        "rule_id": "SAST-005",
        "title": "Weak cryptographic hash (MD5/SHA1)",
        "severity": "HIGH",
        "path_pattern": r"(crypto|cipher|hash|sign)",
        "cwe": "CWE-327",
        "description": "MD5 or SHA1 detected in cryptographic context.",
        "remediation": "Replace with SHA-256 or stronger. Never use MD5/SHA1 for security purposes.",
    },
    {
        "rule_id": "SAST-006",
        "title": "Sensitive data logged",
        "severity": "HIGH",
        "path_pattern": r"(payment|transaction|card|pan|token|account)",
        "cwe": "CWE-532",
        "description": "Payment-related identifiers may be written to log output.",
        "remediation": "Redact PAN, token_id, and account numbers before logging.",
    },
    {
        "rule_id": "SAST-007",
        "title": "Dependency with known CVE",
        "severity": "HIGH",
        "path_pattern": r"(requirements|pyproject|package\.json|pom\.xml|build\.gradle)",
        "cwe": "CWE-1104",
        "description": "One or more dependencies may have known CVEs (mock finding).",
        "remediation": "Run SCA scan and update vulnerable packages.",
    },
]


class MockSASTScanner:
    """
    Deterministic mock SAST scanner.

    Matches file paths against pre-canned rule patterns and returns
    any matching findings. Used by SecurityAgent in local/test mode.
    """

    def scan_files(self, file_paths: list[str]) -> list[dict[str, Any]]:
        """
        Scan a list of file paths and return mock SAST findings.

        Args:
            file_paths: List of relative file paths from repo root.

        Returns:
            List of finding dicts with keys: rule_id, title, severity,
            cwe, description, remediation, affected_file.
        """
        results: list[dict[str, Any]] = []
        seen_rules: set[str] = set()

        for fp in file_paths:
            for rule in _MOCK_SAST_FINDINGS:
                if rule["rule_id"] in seen_rules:
                    continue
                if re.search(rule["path_pattern"], fp, re.I):
                    result = dict(rule)
                    result["affected_file"] = fp
                    results.append(result)
                    seen_rules.add(rule["rule_id"])
                    logger.debug(
                        "sast_mock_hit",
                        rule_id=rule["rule_id"],
                        file=fp,
                        severity=rule["severity"],
                    )

        logger.info("sast_scan_complete", files=len(file_paths), findings=len(results))
        return results
