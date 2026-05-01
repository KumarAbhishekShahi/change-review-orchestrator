"""
Policy and Control Agent — Change Review Orchestrator

Responsibilities:
1. Load policy rules from local YAML source
2. Filter rules applicable to this change (by change_type, data_classification,
   asset_category, and concern keywords from the Impact agent)
3. For each applicable rule: check whether the obligation is met
4. Raise a finding for every unmet obligation (policy gap)
5. Capture exact policy reference IDs in every finding
6. Produce a compliance summary with gap count and obligation status

Design: deterministic rule matching — no LLM required.
All matching logic uses set intersection on enum values and string keywords.
"""

from __future__ import annotations

from typing import Any

import structlog

from change_review_orchestrator.agents.base import BaseAgent
from change_review_orchestrator.domain.enums import (
    AssetCategory,
    ChangeType,
    DataClassification,
    FindingCategory,
    Severity,
)
from change_review_orchestrator.domain.models import (
    AgentResult,
    EvidenceItem,
    Finding,
    WorkflowState,
)
from change_review_orchestrator.integrations.mock.policy_loader_mock import (
    load_policy_rules,
)

logger = structlog.get_logger(__name__)

# Map severity strings from YAML → domain Severity enum
_SEVERITY_MAP: dict[str, Severity] = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH":     Severity.HIGH,
    "MEDIUM":   Severity.MEDIUM,
    "LOW":      Severity.LOW,
    "INFO":     Severity.INFO,
}


def _rule_applies(
    rule: dict[str, Any],
    change_type: ChangeType,
    data_classification: DataClassification,
    asset_categories: set[str],
    concerns: set[str],
) -> bool:
    """
    Determine whether a policy rule is applicable to the current change.

    A rule applies when ALL of the following hold:
    - change_type is in rule.applies_to.change_types (or wildcard "*")
    - data_classification is in rule.applies_to.data_classifications (or wildcard "*")
    - If rule.applies_to.asset_categories is non-empty, at least one changed
      file's category must be in the list
    - If rule.applies_to.concerns is non-empty, at least one concern from the
      impact agent must match

    Args:
        rule:                Policy rule dict from the YAML loader.
        change_type:         ChangeType of the current case.
        data_classification: DataClassification of the current case.
        asset_categories:    Set of AssetCategory values (strings) in changed files.
        concerns:            Set of concern labels from ImpactAgent metadata.

    Returns:
        True if the rule applies.
    """
    applies_to = rule.get("applies_to", {})

    # Check change_type
    allowed_types: list[str] = applies_to.get("change_types", ["*"])
    if "*" not in allowed_types and change_type.value not in allowed_types:
        return False

    # Check data_classification
    allowed_dcs: list[str] = applies_to.get("data_classifications", ["*"])
    if "*" not in allowed_dcs and data_classification.value not in allowed_dcs:
        return False

    # Check asset_categories (empty list = applies regardless of categories)
    required_cats: list[str] = applies_to.get("asset_categories", [])
    if required_cats and not asset_categories.intersection(set(required_cats)):
        return False

    # Check concerns (empty list = applies regardless of concerns)
    required_concerns: list[str] = applies_to.get("concerns", [])
    if required_concerns and not concerns.intersection(set(required_concerns)):
        return False

    return True


def _check_obligation(
    rule: dict[str, Any],
    case_data: dict[str, Any],
) -> tuple[bool, str]:
    """
    Check whether a policy obligation is satisfied.

    Uses gap_check (dot-notation field path on the case) and
    gap_check_truthy to determine pass/fail.

    When gap_check_truthy=True  → field must be non-empty/truthy to PASS
    When gap_check_truthy=False → field must be falsy/empty to PASS
    When gap_check is null      → obligation is assumed unverifiable (warn)

    Args:
        rule:      Policy rule dict.
        case_data: ChangeCase serialised as a dict.

    Returns:
        (obligation_met: bool, reason: str)
    """
    gap_check: str | None = rule.get("gap_check")
    must_be_truthy: bool = rule.get("gap_check_truthy", True)

    if gap_check is None:
        # Cannot verify automatically — flag as a manual check required
        return False, "Obligation requires manual verification (no automatic check defined)"

    # Navigate dot-notation path (e.g. "case.reviewers")
    value: Any = case_data
    for part in gap_check.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            break

    field_is_truthy = bool(value)

    if must_be_truthy:
        met = field_is_truthy
        reason = (
            f"Field '{gap_check}' is present and non-empty."
            if met
            else f"Field '{gap_check}' is missing or empty — obligation not met."
        )
    else:
        # Obligation is: this condition should NOT be true (e.g. has_breaking_changes=False)
        met = not field_is_truthy
        reason = (
            f"Field '{gap_check}' is appropriately absent/false."
            if met
            else f"Field '{gap_check}' is present/true — obligation requires it to be absent."
        )

    return met, reason


class PolicyAgent(BaseAgent):
    """
    Policy and Control Agent.

    Loads policy rules from a YAML source, matches applicable rules to the
    current change, and raises a finding for every unmet obligation.
    Every finding carries the exact policy reference ID for audit traceability.
    """

    agent_name = "policy"

    def __init__(self, policy_file: Any = None) -> None:
        """
        Args:
            policy_file: Optional Path to a custom policy YAML file.
                         Defaults to the bundled tests/fixtures/policy_rules.yaml.
        """
        self._policy_file = policy_file

    def _execute(self, state: WorkflowState, result: AgentResult) -> AgentResult:
        """
        Match policy rules to the current change and flag any gaps.
        """
        case = state.case
        log = logger.bind(agent=self.agent_name, case_id=case.case_id)
        log.info("policy_check_started", change_type=case.change_type.value,
                 data_classification=case.data_classification.value)

        # ── Load rules ────────────────────────────────────────────────────────
        try:
            rules = load_policy_rules(self._policy_file)
        except FileNotFoundError as exc:
            result.error_message = str(exc)
            log.error("policy_file_not_found", error=str(exc))
            result.summary = "Policy check skipped — policy file not found."
            return result

        # ── Build lookup sets from the current case ───────────────────────────
        asset_categories: set[str] = {
            cf.category.value for cf in case.changed_files
        }

        # Pull concerns detected by ImpactAgent (if run)
        impact_result = state.agent_results.get("impact")
        concerns: set[str] = set()
        if impact_result and "all_concerns" in impact_result.metadata:
            concerns = set(impact_result.metadata["all_concerns"])

        # Serialise case for gap_check field navigation
        case_dict = case.model_dump()

        log.debug(
            "policy_context",
            asset_categories=sorted(asset_categories),
            concerns=sorted(concerns),
        )

        # ── Match and evaluate rules ──────────────────────────────────────────
        applicable: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        satisfied: list[dict[str, Any]] = []

        for rule in rules:
            if not _rule_applies(
                rule,
                case.change_type,
                case.data_classification,
                asset_categories,
                concerns,
            ):
                continue

            applicable.append(rule)
            met, reason = _check_obligation(rule, case_dict)

            log.debug(
                "policy_rule_evaluated",
                rule_id=rule["id"],
                met=met,
                reason=reason,
            )

            if met:
                satisfied.append({"rule_id": rule["id"], "reason": reason})
            else:
                gaps.append({
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "obligation": rule["obligation"],
                    "reason": reason,
                })
                severity = _SEVERITY_MAP.get(rule.get("severity", "MEDIUM"), Severity.MEDIUM)
                result.findings.append(Finding(
                    agent=self.agent_name,
                    category=FindingCategory.POLICY,
                    severity=severity,
                    title=f"Policy gap: {rule['title']}",
                    description=(
                        f"{rule['description'].strip()} "
                        f"Obligation '{rule['obligation']}' not satisfied. "
                        f"Reason: {reason}"
                    ),
                    policy_reference=rule["id"],
                    remediation_guidance=(
                        f"Satisfy obligation '{rule['obligation']}' to comply with {rule['id']}. "
                        f"See policy: {rule['title']}."
                    ),
                    affected_assets=[],
                ))
                log.warning(
                    "policy_gap_detected",
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    obligation=rule["obligation"],
                )

        # ── Persist structured results ────────────────────────────────────────
        result.metadata["applicable_rules"] = len(applicable)
        result.metadata["satisfied_rules"] = len(satisfied)
        result.metadata["gap_count"] = len(gaps)
        result.metadata["gaps"] = gaps
        result.metadata["satisfied"] = satisfied
        result.metadata["policy_references"] = [r["id"] for r in applicable]

        result.evidence_items.append(EvidenceItem(
            source_agent=self.agent_name,
            label="Policy Obligation Audit",
            content_summary=(
                f"{len(applicable)} applicable policies evaluated. "
                f"{len(satisfied)} satisfied, {len(gaps)} gaps detected. "
                f"Policy IDs checked: {', '.join(r['id'] for r in applicable)}."
            ),
        ))

        result.summary = (
            f"Policy check complete. {len(applicable)} applicable rules. "
            f"Satisfied: {len(satisfied)}. Gaps: {len(gaps)}. "
            f"Gap details: {[g['rule_id'] for g in gaps] or 'none'}."
        )

        log.info(
            "policy_check_complete",
            applicable=len(applicable),
            satisfied=len(satisfied),
            gaps=len(gaps),
        )
        return result
