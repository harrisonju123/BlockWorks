"""Tests for workflow YAML/JSON serialization round-trips.

Validates that workflow definitions survive ser/deser without
data loss, and that malformed input is rejected with clear errors.
"""

from __future__ import annotations

import json

import pytest

from agentproof.workflows.serialization import (
    SerializationError,
    from_json,
    from_yaml,
    to_json,
    to_yaml,
)
from agentproof.workflows.types import (
    StepType,
    WorkflowDefinition,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf-test-1",
        name="Test Pipeline",
        description="A test workflow",
        owner="test-owner",
        version=2,
        steps=[
            WorkflowStep(
                id="extract",
                listing_id="l-extractor",
                step_type=StepType.AGENT,
                inputs={"url": "https://example.com"},
                outputs={"text": "extracted_text"},
            ),
            WorkflowStep(
                id="transform",
                listing_id="l-transformer",
                step_type=StepType.MCP_TOOL,
                depends_on=["extract"],
                inputs={"format": "markdown"},
            ),
            WorkflowStep(
                id="load",
                listing_id="l-loader",
                step_type=StepType.AGENT,
                depends_on=["transform"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


class TestJsonSerialization:

    def test_round_trip_preserves_data(self) -> None:
        original = _sample_workflow()
        json_str = to_json(original)
        restored = from_json(json_str)

        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.owner == original.owner
        assert restored.version == original.version
        assert len(restored.steps) == len(original.steps)

    def test_round_trip_preserves_step_details(self) -> None:
        original = _sample_workflow()
        restored = from_json(to_json(original))

        for orig_step, rest_step in zip(original.steps, restored.steps):
            assert rest_step.id == orig_step.id
            assert rest_step.listing_id == orig_step.listing_id
            assert rest_step.step_type == orig_step.step_type
            assert rest_step.inputs == orig_step.inputs
            assert rest_step.outputs == orig_step.outputs
            assert rest_step.depends_on == orig_step.depends_on

    def test_produces_valid_json(self) -> None:
        json_str = to_json(_sample_workflow())
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert "name" in parsed
        assert "steps" in parsed

    def test_from_json_rejects_invalid_json(self) -> None:
        with pytest.raises(SerializationError, match="Invalid JSON"):
            from_json("not valid json {{{")

    def test_from_json_rejects_missing_required_fields(self) -> None:
        with pytest.raises(SerializationError, match="Schema validation"):
            from_json('{"description": "no name or steps"}')

    def test_from_json_rejects_bad_step_type(self) -> None:
        bad = json.dumps({
            "name": "test",
            "steps": [{
                "id": "s1",
                "listing_id": "l1",
                "step_type": "invalid_type",
            }],
        })
        with pytest.raises(SerializationError, match="Schema validation"):
            from_json(bad)

    def test_minimal_workflow_round_trip(self) -> None:
        """Smallest valid workflow should survive round-trip."""
        minimal = WorkflowDefinition(
            name="tiny",
            steps=[WorkflowStep(id="s1", listing_id="l1", step_type=StepType.AGENT)],
        )
        restored = from_json(to_json(minimal))
        assert restored.name == "tiny"
        assert len(restored.steps) == 1


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


class TestYamlSerialization:

    def test_round_trip_preserves_data(self) -> None:
        original = _sample_workflow()
        yaml_str = to_yaml(original)
        restored = from_yaml(yaml_str)

        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.version == original.version
        assert len(restored.steps) == len(original.steps)

    def test_round_trip_preserves_step_details(self) -> None:
        original = _sample_workflow()
        restored = from_yaml(to_yaml(original))

        for orig_step, rest_step in zip(original.steps, restored.steps):
            assert rest_step.id == orig_step.id
            assert rest_step.listing_id == orig_step.listing_id
            assert rest_step.step_type == orig_step.step_type
            assert rest_step.depends_on == orig_step.depends_on

    def test_yaml_is_human_readable(self) -> None:
        """YAML output should contain readable key names, not compact JSON."""
        yaml_str = to_yaml(_sample_workflow())
        assert "name:" in yaml_str
        assert "steps:" in yaml_str
        assert "listing_id:" in yaml_str

    def test_from_yaml_rejects_invalid_yaml(self) -> None:
        with pytest.raises(SerializationError, match="Invalid YAML"):
            from_yaml("{{{\n  invalid: yaml: content")

    def test_from_yaml_rejects_non_mapping(self) -> None:
        with pytest.raises(SerializationError, match="root must be a mapping"):
            from_yaml("- just\n- a\n- list")

    def test_from_yaml_rejects_missing_fields(self) -> None:
        with pytest.raises(SerializationError, match="Schema validation"):
            from_yaml("description: no name\n")

    def test_minimal_workflow_round_trip(self) -> None:
        minimal = WorkflowDefinition(
            name="tiny",
            steps=[WorkflowStep(id="s1", listing_id="l1", step_type=StepType.AGENT)],
        )
        restored = from_yaml(to_yaml(minimal))
        assert restored.name == "tiny"
        assert len(restored.steps) == 1


# ---------------------------------------------------------------------------
# Cross-format
# ---------------------------------------------------------------------------


class TestCrossFormat:

    def test_json_to_yaml_to_json(self) -> None:
        """Workflow should survive JSON -> YAML -> JSON conversion."""
        original = _sample_workflow()
        json_str = to_json(original)
        intermediate = from_json(json_str)
        yaml_str = to_yaml(intermediate)
        from_yaml_def = from_yaml(yaml_str)
        final_json = to_json(from_yaml_def)
        final = from_json(final_json)

        assert final.name == original.name
        assert len(final.steps) == len(original.steps)
        for orig, fin in zip(original.steps, final.steps):
            assert fin.id == orig.id
            assert fin.listing_id == orig.listing_id
