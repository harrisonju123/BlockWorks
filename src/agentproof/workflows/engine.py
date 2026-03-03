"""Workflow execution engine with DAG validation and parallel step execution.

Validates workflow definitions for structural correctness (cycles,
missing deps, unknown listings), then executes steps in topological
order with asyncio.gather for independent steps at each level.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime

from agentproof.config import get_config
from agentproof.utils import utcnow
from agentproof.registry.store import ListingNotFoundError, RegistryStore
from agentproof.workflows.types import (
    StepResult,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStep,
)


class WorkflowValidationError(Exception):
    """Raised when a workflow definition is structurally invalid."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Workflow validation failed: {'; '.join(errors)}")


class WorkflowExecutionError(Exception):
    """Raised when a workflow fails during execution."""


def validate_workflow(
    definition: WorkflowDefinition,
    registry: RegistryStore | None = None,
) -> list[str]:
    """Check a workflow for structural problems.

    Returns a list of human-readable error strings. Empty list means valid.
    Checks performed:
      - No duplicate step IDs
      - All depends_on references point to existing step IDs
      - No cycles in the DAG (DFS-based detection)
      - Step count within configured max
      - All listing_ids exist in the registry (if provided)
    """
    errors: list[str] = []
    cfg = get_config()

    if not definition.steps:
        errors.append("Workflow must have at least one step")
        return errors

    if len(definition.steps) > cfg.workflows_max_steps:
        errors.append(
            f"Workflow has {len(definition.steps)} steps, "
            f"max allowed is {cfg.workflows_max_steps}"
        )

    step_ids = {step.id for step in definition.steps}

    # Duplicate step IDs
    if len(step_ids) != len(definition.steps):
        seen: set[str] = set()
        for step in definition.steps:
            if step.id in seen:
                errors.append(f"Duplicate step ID: {step.id}")
            seen.add(step.id)

    # Missing dependency references
    for step in definition.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                errors.append(
                    f"Step '{step.id}' depends on unknown step '{dep}'"
                )

    # Cycle detection via DFS
    cycle_errors = _detect_cycles(definition.steps)
    errors.extend(cycle_errors)

    # Registry listing validation
    if registry is not None:
        for step in definition.steps:
            try:
                registry.get_listing(step.listing_id)
            except ListingNotFoundError:
                errors.append(
                    f"Step '{step.id}' references unknown listing '{step.listing_id}'"
                )

    return errors


def _detect_cycles(steps: list[WorkflowStep]) -> list[str]:
    """DFS-based cycle detection on the step dependency graph.

    Returns error messages for each cycle found, or empty list if acyclic.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    for step in steps:
        for dep in step.depends_on:
            adj[dep].append(step.id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {step.id: WHITE for step in steps}
    errors: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                errors.append(f"Cycle detected involving step '{neighbor}'")
            elif color[neighbor] == WHITE:
                dfs(neighbor)
        color[node] = BLACK

    for step in steps:
        if color[step.id] == WHITE:
            dfs(step.id)

    return errors


def topological_sort(steps: list[WorkflowStep]) -> list[list[str]]:
    """Group steps into execution levels via Kahn's algorithm.

    Returns a list of levels, where each level contains step IDs
    that can execute in parallel (all their dependencies are in
    earlier levels).
    """
    in_degree: dict[str, int] = {step.id: 0 for step in steps}
    dependents: dict[str, list[str]] = defaultdict(list)

    for step in steps:
        for dep in step.depends_on:
            dependents[dep].append(step.id)
            in_degree[step.id] += 1

    # Start with steps that have no dependencies
    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    levels: list[list[str]] = []

    while queue:
        levels.append(sorted(queue))  # sorted for determinism
        next_queue: list[str] = []
        for sid in queue:
            for dependent in dependents.get(sid, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_queue.append(dependent)
        queue = next_queue

    return levels


async def execute_workflow(
    definition: WorkflowDefinition,
    inputs: dict[str, str],
    registry: RegistryStore | None = None,
    step_executor: StepExecutor | None = None,
) -> WorkflowExecution:
    """Execute a validated workflow definition.

    Steps run in topological order, with independent steps at each
    level executing concurrently via asyncio.gather. Outputs from
    completed steps are passed as inputs to dependent steps.
    """
    cfg = get_config()

    # Validate first
    errors = validate_workflow(definition, registry)
    if errors:
        raise WorkflowValidationError(errors)

    executor = step_executor or _default_step_executor

    execution = WorkflowExecution(
        id=str(uuid.uuid4()),
        workflow_id=definition.id,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=utcnow(),
        trace_id=str(uuid.uuid4()),
    )

    step_map = {step.id: step for step in definition.steps}
    levels = topological_sort(definition.steps)
    # Accumulated outputs from all completed steps, keyed by step_id
    completed_outputs: dict[str, dict[str, str]] = {}
    all_results: list[StepResult] = []

    try:
        async with asyncio.timeout(cfg.workflows_execution_timeout_s):
            for level in levels:
                tasks = []
                for step_id in level:
                    step = step_map[step_id]
                    # Build input dict: merge workflow inputs with outputs
                    # from dependency steps
                    step_inputs = dict(inputs)
                    for dep_id in step.depends_on:
                        if dep_id in completed_outputs:
                            for k, v in completed_outputs[dep_id].items():
                                step_inputs[k] = v
                    # Also merge the step's own declared inputs
                    step_inputs.update(step.inputs)

                    tasks.append(executor(step, step_inputs))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for step_id, result in zip(level, results):
                    if isinstance(result, BaseException):
                        # Step failed — mark execution as failed
                        fail_result = StepResult(
                            step_id=step_id,
                            status=WorkflowExecutionStatus.FAILED,
                            output={"error": str(result)},
                        )
                        all_results.append(fail_result)
                        execution.step_results = all_results
                        execution.status = WorkflowExecutionStatus.FAILED
                        execution.completed_at = utcnow()
                        execution.total_cost = sum(r.cost for r in all_results)
                        return execution

                    all_results.append(result)
                    completed_outputs[step_id] = result.output
                    execution.steps_completed += 1

    except TimeoutError:
        execution.status = WorkflowExecutionStatus.FAILED
        execution.completed_at = utcnow()
        execution.step_results = all_results
        execution.total_cost = sum(r.cost for r in all_results)
        return execution

    execution.status = WorkflowExecutionStatus.COMPLETED
    execution.completed_at = utcnow()
    execution.step_results = all_results
    execution.total_cost = sum(r.cost for r in all_results)
    return execution


# ---------------------------------------------------------------------------
# Step executor protocol
# ---------------------------------------------------------------------------

# Type alias for a callable that executes a single step
type StepExecutor = callable  # (WorkflowStep, dict) -> StepResult


async def _default_step_executor(
    step: WorkflowStep,
    inputs: dict[str, str],
) -> StepResult:
    """Default no-op executor that passes inputs through as outputs.

    Real implementations would call the listing's endpoint_url,
    invoke an agent, or make MCP tool calls. This default exists
    so the engine can run without external services for testing
    and local development.
    """
    start = time.perf_counter()
    started_at = utcnow()

    # Simulate a small cost per step
    cost = 0.001

    elapsed_ms = (time.perf_counter() - start) * 1000

    return StepResult(
        step_id=step.id,
        status=WorkflowExecutionStatus.COMPLETED,
        output=dict(inputs),
        cost=cost,
        latency_ms=elapsed_ms,
        started_at=started_at,
        completed_at=utcnow(),
    )
