"""YAML and JSON serialization for workflow definitions.

Supports round-trip serialization with validation on deserialization
so that workflows loaded from external sources are always structurally
sound before they reach the engine.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from blockthrough.workflows.types import WorkflowDefinition

# PyYAML is optional — workflows can use JSON-only mode when YAML
# is not installed (e.g., in minimal Docker images)
try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


class SerializationError(Exception):
    """Raised when serialization or deserialization fails."""


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def to_json(definition: WorkflowDefinition) -> str:
    """Serialize a WorkflowDefinition to a JSON string."""
    return definition.model_dump_json(indent=2)


def from_json(json_str: str) -> WorkflowDefinition:
    """Deserialize a WorkflowDefinition from a JSON string.

    Raises SerializationError on invalid JSON or schema violations.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise SerializationError(f"Invalid JSON: {e}") from e

    try:
        return WorkflowDefinition.model_validate(data)
    except ValidationError as e:
        raise SerializationError(f"Schema validation failed: {e}") from e


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


def to_yaml(definition: WorkflowDefinition) -> str:
    """Serialize a WorkflowDefinition to a YAML string.

    Raises SerializationError if PyYAML is not installed.
    """
    if not _HAS_YAML:
        raise SerializationError(
            "PyYAML is required for YAML serialization. "
            "Install it with: pip install pyyaml"
        )

    data = definition.model_dump(mode="json")
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def from_yaml(yaml_str: str) -> WorkflowDefinition:
    """Deserialize a WorkflowDefinition from a YAML string.

    Raises SerializationError if PyYAML is not installed, the YAML
    is malformed, or the data doesn't match the schema.
    """
    if not _HAS_YAML:
        raise SerializationError(
            "PyYAML is required for YAML deserialization. "
            "Install it with: pip install pyyaml"
        )

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise SerializationError(f"Invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise SerializationError("YAML root must be a mapping")

    try:
        return WorkflowDefinition.model_validate(data)
    except ValidationError as e:
        raise SerializationError(f"Schema validation failed: {e}") from e
