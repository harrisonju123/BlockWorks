"""Tests for workflow type models — construction, defaults, validation."""

from __future__ import annotations

from datetime import datetime, timezone

from blockthrough.workflows.types import (
    PaymentSplit,
    StepResult,
    StepType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStep,
)


class TestWorkflowStep:

    def test_minimal_construction(self) -> None:
        step = WorkflowStep(id="s1", listing_id="listing-1", step_type=StepType.AGENT)
        assert step.id == "s1"
        assert step.listing_id == "listing-1"
        assert step.step_type == StepType.AGENT
        assert step.inputs == {}
        assert step.outputs == {}
        assert step.depends_on == []

    def test_with_dependencies(self) -> None:
        step = WorkflowStep(
            id="s2",
            listing_id="listing-2",
            step_type=StepType.MCP_TOOL,
            depends_on=["s1"],
        )
        assert step.depends_on == ["s1"]

    def test_with_inputs_and_outputs(self) -> None:
        step = WorkflowStep(
            id="s1",
            listing_id="l1",
            step_type=StepType.AGENT,
            inputs={"prompt": "hello"},
            outputs={"result": "text"},
        )
        assert step.inputs["prompt"] == "hello"
        assert step.outputs["result"] == "text"


class TestWorkflowDefinition:

    def test_minimal_construction(self) -> None:
        defn = WorkflowDefinition(
            name="test-wf",
            steps=[
                WorkflowStep(id="s1", listing_id="l1", step_type=StepType.AGENT),
            ],
        )
        assert defn.name == "test-wf"
        assert defn.id == ""
        assert defn.version == 1
        assert len(defn.steps) == 1

    def test_defaults(self) -> None:
        defn = WorkflowDefinition(
            name="test",
            steps=[WorkflowStep(id="s1", listing_id="l1", step_type=StepType.AGENT)],
        )
        assert defn.description == ""
        assert defn.owner == ""
        assert defn.created_at is None


class TestStepResult:

    def test_construction(self) -> None:
        result = StepResult(
            step_id="s1",
            status=WorkflowExecutionStatus.COMPLETED,
            output={"text": "hello"},
            cost=0.005,
            latency_ms=42.0,
        )
        assert result.step_id == "s1"
        assert result.cost == 0.005
        assert result.output["text"] == "hello"

    def test_defaults(self) -> None:
        result = StepResult(
            step_id="s1",
            status=WorkflowExecutionStatus.PENDING,
        )
        assert result.output == {}
        assert result.cost == 0.0
        assert result.latency_ms == 0.0
        assert result.started_at is None


class TestWorkflowExecution:

    def test_construction(self) -> None:
        ex = WorkflowExecution(
            id="exec-1",
            workflow_id="wf-1",
            status=WorkflowExecutionStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        assert ex.id == "exec-1"
        assert ex.status == WorkflowExecutionStatus.RUNNING
        assert ex.steps_completed == 0
        assert ex.total_cost == 0.0

    def test_defaults(self) -> None:
        ex = WorkflowExecution(workflow_id="wf-1")
        assert ex.status == WorkflowExecutionStatus.PENDING
        assert ex.step_results == []
        assert ex.trace_id == ""


class TestPaymentSplit:

    def test_construction(self) -> None:
        split = PaymentSplit(
            step_id="s1",
            listing_id="l1",
            amount=0.05,
            percentage_of_total=50.0,
        )
        assert split.step_id == "s1"
        assert split.amount == 0.05
        assert split.percentage_of_total == 50.0
