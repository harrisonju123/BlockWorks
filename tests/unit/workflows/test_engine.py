"""Tests for the workflow execution engine.

Covers DAG validation (cycles, missing deps, step limits),
topological sort correctness, and async parallel execution.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from blockthrough.config import get_config
from blockthrough.registry.store import RegistryStore
from blockthrough.registry.types import AgentListing, ListingCategory
from blockthrough.workflows.engine import (
    WorkflowValidationError,
    _detect_cycles,
    execute_workflow,
    topological_sort,
    validate_workflow,
)
from blockthrough.workflows.types import (
    StepResult,
    StepType,
    WorkflowDefinition,
    WorkflowExecutionStatus,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _step(sid: str, deps: list[str] | None = None, listing_id: str = "l1") -> WorkflowStep:
    return WorkflowStep(
        id=sid,
        listing_id=listing_id,
        step_type=StepType.AGENT,
        depends_on=deps or [],
    )


def _workflow(steps: list[WorkflowStep], name: str = "test-wf") -> WorkflowDefinition:
    return WorkflowDefinition(name=name, steps=steps)


def _make_registry_with_listings(*listing_ids: str) -> RegistryStore:
    """Build a RegistryStore pre-populated with active listings."""
    store = RegistryStore(min_stake=0.0)
    now = datetime.now(timezone.utc)
    for lid in listing_ids:
        listing = AgentListing(
            id="",
            name=f"Agent-{lid}",
            description="test",
            owner_address="0xowner",
            category=ListingCategory.AGENT,
            stake_amount=0.0,
            registered_at=now,
            last_active=now,
        )
        created = store.register_listing(listing)
        # Overwrite the auto-generated ID so we can reference it by our chosen ID.
        # This is a test convenience — production code uses UUIDs.
        store._listings[lid] = store._listings.pop(created.id)
        data = store._listings[lid].model_dump()
        data["id"] = lid
        store._listings[lid] = AgentListing(**data)
    return store


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateWorkflow:

    def setup_method(self) -> None:
        get_config.cache_clear()

    def test_valid_linear_dag(self) -> None:
        errors = validate_workflow(
            _workflow([_step("a"), _step("b", ["a"]), _step("c", ["b"])])
        )
        assert errors == []

    def test_valid_diamond_dag(self) -> None:
        errors = validate_workflow(
            _workflow([
                _step("a"),
                _step("b", ["a"]),
                _step("c", ["a"]),
                _step("d", ["b", "c"]),
            ])
        )
        assert errors == []

    def test_empty_workflow_rejected(self) -> None:
        errors = validate_workflow(_workflow([]))
        assert any("at least one step" in e for e in errors)

    def test_duplicate_step_ids_detected(self) -> None:
        errors = validate_workflow(
            _workflow([_step("a"), _step("a")])
        )
        assert any("Duplicate step ID" in e for e in errors)

    def test_missing_dependency_detected(self) -> None:
        errors = validate_workflow(
            _workflow([_step("a", ["nonexistent"])])
        )
        assert any("unknown step" in e for e in errors)

    def test_self_dependency_cycle_detected(self) -> None:
        errors = validate_workflow(
            _workflow([_step("a", ["a"])])
        )
        assert any("Cycle" in e for e in errors)

    def test_two_node_cycle_detected(self) -> None:
        errors = validate_workflow(
            _workflow([_step("a", ["b"]), _step("b", ["a"])])
        )
        assert any("Cycle" in e for e in errors)

    def test_three_node_cycle_detected(self) -> None:
        errors = validate_workflow(
            _workflow([
                _step("a", ["c"]),
                _step("b", ["a"]),
                _step("c", ["b"]),
            ])
        )
        assert any("Cycle" in e for e in errors)

    def test_exceeds_max_steps(self) -> None:
        cfg = get_config()
        steps = [_step(f"s{i}") for i in range(cfg.workflows_max_steps + 1)]
        errors = validate_workflow(_workflow(steps))
        assert any("max allowed" in e for e in errors)

    def test_within_max_steps(self) -> None:
        cfg = get_config()
        steps = [_step(f"s{i}") for i in range(cfg.workflows_max_steps)]
        errors = validate_workflow(_workflow(steps))
        assert errors == []

    def test_unknown_listing_detected_with_registry(self) -> None:
        registry = _make_registry_with_listings("real-listing")
        errors = validate_workflow(
            _workflow([_step("a", listing_id="fake-listing")]),
            registry=registry,
        )
        assert any("unknown listing" in e for e in errors)

    def test_valid_listing_passes_registry_check(self) -> None:
        registry = _make_registry_with_listings("real-listing")
        errors = validate_workflow(
            _workflow([_step("a", listing_id="real-listing")]),
            registry=registry,
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Cycle detection (isolated)
# ---------------------------------------------------------------------------


class TestDetectCycles:

    def test_no_cycle(self) -> None:
        steps = [_step("a"), _step("b", ["a"])]
        assert _detect_cycles(steps) == []

    def test_simple_cycle(self) -> None:
        steps = [_step("a", ["b"]), _step("b", ["a"])]
        errors = _detect_cycles(steps)
        assert len(errors) > 0

    def test_disconnected_graph_no_cycle(self) -> None:
        steps = [_step("a"), _step("b"), _step("c")]
        assert _detect_cycles(steps) == []


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:

    def test_single_step(self) -> None:
        levels = topological_sort([_step("a")])
        assert levels == [["a"]]

    def test_linear_chain(self) -> None:
        levels = topological_sort([
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        ])
        assert levels == [["a"], ["b"], ["c"]]

    def test_parallel_steps_same_level(self) -> None:
        levels = topological_sort([
            _step("a"),
            _step("b"),
            _step("c"),
        ])
        # All independent — should be in one level
        assert len(levels) == 1
        assert sorted(levels[0]) == ["a", "b", "c"]

    def test_diamond_dag(self) -> None:
        levels = topological_sort([
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ])
        assert levels[0] == ["a"]
        assert sorted(levels[1]) == ["b", "c"]
        assert levels[2] == ["d"]

    def test_fan_out_fan_in(self) -> None:
        """Two roots fan into a joiner."""
        levels = topological_sort([
            _step("r1"),
            _step("r2"),
            _step("join", ["r1", "r2"]),
        ])
        assert len(levels) == 2
        assert sorted(levels[0]) == ["r1", "r2"]
        assert levels[1] == ["join"]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecuteWorkflow:

    def setup_method(self) -> None:
        get_config.cache_clear()

    @pytest.mark.asyncio
    async def test_single_step_execution(self) -> None:
        wf = _workflow([_step("a")])
        execution = await execute_workflow(wf, {"input": "hello"})

        assert execution.status == WorkflowExecutionStatus.COMPLETED
        assert execution.steps_completed == 1
        assert len(execution.step_results) == 1
        assert execution.step_results[0].step_id == "a"
        assert execution.total_cost > 0

    @pytest.mark.asyncio
    async def test_linear_chain_execution(self) -> None:
        wf = _workflow([
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        ])
        execution = await execute_workflow(wf, {"data": "start"})

        assert execution.status == WorkflowExecutionStatus.COMPLETED
        assert execution.steps_completed == 3
        assert len(execution.step_results) == 3

    @pytest.mark.asyncio
    async def test_parallel_steps_execute_concurrently(self) -> None:
        """Independent steps at the same level should run via gather."""
        wf = _workflow([
            _step("a"),
            _step("b"),
            _step("c"),
        ])
        execution = await execute_workflow(wf, {})

        assert execution.status == WorkflowExecutionStatus.COMPLETED
        assert execution.steps_completed == 3

    @pytest.mark.asyncio
    async def test_diamond_dag_execution(self) -> None:
        wf = _workflow([
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ])
        execution = await execute_workflow(wf, {"seed": "value"})

        assert execution.status == WorkflowExecutionStatus.COMPLETED
        assert execution.steps_completed == 4

    @pytest.mark.asyncio
    async def test_outputs_propagate_to_dependents(self) -> None:
        """Outputs from step A should appear in step B's inputs."""
        call_log: list[tuple[str, dict]] = []

        async def tracking_executor(step: WorkflowStep, inputs: dict) -> StepResult:
            call_log.append((step.id, dict(inputs)))
            return StepResult(
                step_id=step.id,
                status=WorkflowExecutionStatus.COMPLETED,
                output={"from_" + step.id: "produced"},
                cost=0.001,
            )

        wf = _workflow([_step("a"), _step("b", ["a"])])
        await execute_workflow(wf, {"initial": "val"}, step_executor=tracking_executor)

        # Step B should have received outputs from step A
        b_inputs = [inputs for sid, inputs in call_log if sid == "b"][0]
        assert "from_a" in b_inputs

    @pytest.mark.asyncio
    async def test_step_failure_marks_execution_failed(self) -> None:
        async def failing_executor(step: WorkflowStep, inputs: dict) -> StepResult:
            if step.id == "b":
                raise RuntimeError("step b broke")
            return StepResult(
                step_id=step.id,
                status=WorkflowExecutionStatus.COMPLETED,
                output={},
                cost=0.001,
            )

        wf = _workflow([_step("a"), _step("b", ["a"])])
        execution = await execute_workflow(wf, {}, step_executor=failing_executor)

        assert execution.status == WorkflowExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_cost_tracking_across_steps(self) -> None:
        async def costed_executor(step: WorkflowStep, inputs: dict) -> StepResult:
            costs = {"a": 0.01, "b": 0.02, "c": 0.03}
            return StepResult(
                step_id=step.id,
                status=WorkflowExecutionStatus.COMPLETED,
                output={},
                cost=costs.get(step.id, 0.0),
            )

        wf = _workflow([_step("a"), _step("b"), _step("c")])
        execution = await execute_workflow(wf, {}, step_executor=costed_executor)

        assert execution.total_cost == pytest.approx(0.06)

    @pytest.mark.asyncio
    async def test_execution_has_trace_id(self) -> None:
        wf = _workflow([_step("a")])
        execution = await execute_workflow(wf, {})
        assert execution.trace_id != ""
        assert len(execution.trace_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_execution_has_timestamps(self) -> None:
        wf = _workflow([_step("a")])
        execution = await execute_workflow(wf, {})
        assert execution.started_at is not None
        assert execution.completed_at is not None
        assert execution.completed_at >= execution.started_at

    @pytest.mark.asyncio
    async def test_invalid_workflow_raises_validation_error(self) -> None:
        wf = _workflow([])
        with pytest.raises(WorkflowValidationError, match="at least one step"):
            await execute_workflow(wf, {})
