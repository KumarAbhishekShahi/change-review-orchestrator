"""
Policy document loader — loads rules from local YAML/JSON files.

Provides the deterministic policy rule set used by PolicyAgent.
In production this could load from a policy management system (OPA,
Confluence, a regulated policy repo). For now, it reads the bundled
YAML fixture.

The loader is intentionally separated from the agent so rules can be
updated or swapped without touching agent logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

# Default policy file bundled with the project
_DEFAULT_POLICY_FILE = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "tests" / "fixtures" / "policy_rules.yaml"
)


def load_policy_rules(policy_file: Path | None = None) -> list[dict[str, Any]]:
    """
    Load policy rules from a YAML file.

    Args:
        policy_file: Path to a YAML policy file. Defaults to the bundled
                     tests/fixtures/policy_rules.yaml.

    Returns:
        List of policy rule dicts, each with keys: id, title, description,
        applies_to, severity, obligation, gap_check, gap_check_truthy.

    Raises:
        FileNotFoundError: If the policy file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    target = policy_file or _DEFAULT_POLICY_FILE

    if not target.exists():
        raise FileNotFoundError(f"Policy file not found: {target}")

    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    rules: list[dict[str, Any]] = raw.get("policies", [])

    logger.info("policy_rules_loaded", file=str(target), count=len(rules))
    return rules
