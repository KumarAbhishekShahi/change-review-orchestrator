"""
JSON serialisation helpers for domain models.

Provides deterministic, repository-safe serialisation that handles
datetime timezone-awareness, enums, and UUID fields consistently.
Used when writing artefacts to disk and when storing JSON in Postgres.
"""

from __future__ import annotations

import datetime
import json
from typing import Any
from uuid import UUID

import orjson

from change_review_orchestrator.domain.models import WorkflowState, ChangeCase


class _DomainEncoder(json.JSONEncoder):
    """
    Custom JSON encoder for domain objects.

    Handles types not supported by the stdlib encoder:
    - datetime → ISO-8601 string with UTC offset
    - UUID → lowercase hyphenated string
    - Pydantic models → dict via model_dump()
    - Enums → their .value string
    """

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime.datetime):
            # Always serialise as UTC ISO-8601 to avoid timezone ambiguity
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=datetime.timezone.utc)
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        # Pydantic v2 models
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        # Enums
        if hasattr(obj, "value"):
            return obj.value
        return super().default(obj)


def to_json(obj: Any, *, pretty: bool = False) -> str:
    """
    Serialise a domain object (or any JSON-compatible value) to a JSON string.

    Uses orjson for speed when pretty=False, falls back to stdlib for
    pretty-printing (orjson does not support indent).

    Args:
        obj:    The object to serialise.
        pretty: If True, produce indented, human-readable JSON.

    Returns:
        A UTF-8 JSON string.
    """
    if hasattr(obj, "model_dump"):
        # Pydantic v2 — use model_dump for full field visibility
        data = obj.model_dump(mode="json")
    else:
        data = obj

    if pretty:
        return json.dumps(data, indent=2, cls=_DomainEncoder, ensure_ascii=False)

    # orjson produces bytes; decode to str for uniform return type
    return orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS).decode()


def from_json(raw: str | bytes, model: type) -> Any:
    """
    Deserialise a JSON string/bytes into a Pydantic model instance.

    Args:
        raw:   JSON string or bytes.
        model: The Pydantic model class to parse into.

    Returns:
        An instance of `model`.

    Raises:
        pydantic.ValidationError: If the JSON does not match the schema.
    """
    data = orjson.loads(raw)
    return model.model_validate(data)


def workflow_state_to_json(state: WorkflowState, *, pretty: bool = True) -> str:
    """Convenience wrapper: serialise a full WorkflowState to JSON."""
    return to_json(state, pretty=pretty)


def change_case_to_json(case: ChangeCase, *, pretty: bool = True) -> str:
    """Convenience wrapper: serialise a ChangeCase to JSON."""
    return to_json(case, pretty=pretty)
