"""Workflow API endpoints — CRUD, validation, and execution.

Backed by in-memory storage for local development. Workflows are
DAGs of agent and MCP tool steps with automatic payment splitting.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from blockthrough.channels.manager import ChannelManager
from blockthrough.channels.types import ChannelConfig
from blockthrough.config import get_config
from blockthrough.registry.store import RegistryStore
from blockthrough.workflows.engine import (
    WorkflowValidationError,
    execute_workflow,
    validate_workflow,
)
from blockthrough.utils import utcnow
from blockthrough.workflows.payments import calculate_splits, settle_workflow
from blockthrough.workflows.types import (
    StepType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStep,
)

router = APIRouter(prefix="/workflows")

# ---------------------------------------------------------------------------
# Module-level singletons — lazily initialized on first request
# ---------------------------------------------------------------------------

_workflows: dict[str, WorkflowDefinition] = {}
_executions: dict[str, WorkflowExecution] = {}
_registry: RegistryStore | None = None
_channel_manager: ChannelManager | None = None


def _get_registry() -> RegistryStore:
    global _registry
    if _registry is None:
        _registry = RegistryStore()
    return _registry


def _get_channel_manager() -> ChannelManager:
    global _channel_manager
    if _channel_manager is None:
        cfg = get_config()
        _channel_manager = ChannelManager(
            config=ChannelConfig(min_deposit=cfg.channels_min_deposit)
        )
    return _channel_manager


def reset_workflows() -> None:
    """Reset all module-level state. Used by tests."""
    global _workflows, _executions, _registry, _channel_manager
    _workflows = {}
    _executions = {}
    _registry = None
    _channel_manager = None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class StepRequest(BaseModel):
    id: str
    listing_id: str
    step_type: StepType
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    steps: list[StepRequest]
    owner: str = ""


class ExecuteWorkflowRequest(BaseModel):
    inputs: dict[str, str] = Field(default_factory=dict)


class ValidateWorkflowRequest(BaseModel):
    name: str = "validation-check"
    steps: list[StepRequest] = Field(default_factory=list)


class StepResultResponse(BaseModel):
    step_id: str
    status: str
    output: dict[str, str]
    cost: float
    latency_ms: float


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str
    owner: str
    version: int
    step_count: int
    created_at: str | None


class WorkflowDetailResponse(WorkflowResponse):
    steps: list[StepRequest]


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowResponse]
    count: int


class ExecutionResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    started_at: str | None
    completed_at: str | None
    steps_completed: int
    total_cost: float
    trace_id: str
    step_results: list[StepResultResponse]


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str]


class PaymentSplitResponse(BaseModel):
    step_id: str
    listing_id: str
    amount: float
    percentage_of_total: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_to_response(wf: WorkflowDefinition) -> WorkflowResponse:
    return WorkflowResponse(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        owner=wf.owner,
        version=wf.version,
        step_count=len(wf.steps),
        created_at=wf.created_at.isoformat() if wf.created_at else None,
    )


def _workflow_to_detail(wf: WorkflowDefinition) -> WorkflowDetailResponse:
    return WorkflowDetailResponse(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        owner=wf.owner,
        version=wf.version,
        step_count=len(wf.steps),
        created_at=wf.created_at.isoformat() if wf.created_at else None,
        steps=[
            StepRequest(
                id=s.id,
                listing_id=s.listing_id,
                step_type=s.step_type,
                inputs=s.inputs,
                outputs=s.outputs,
                depends_on=s.depends_on,
            )
            for s in wf.steps
        ],
    )


def _execution_to_response(ex: WorkflowExecution) -> ExecutionResponse:
    return ExecutionResponse(
        id=ex.id,
        workflow_id=ex.workflow_id,
        status=ex.status.value,
        started_at=ex.started_at.isoformat() if ex.started_at else None,
        completed_at=ex.completed_at.isoformat() if ex.completed_at else None,
        steps_completed=ex.steps_completed,
        total_cost=ex.total_cost,
        trace_id=ex.trace_id,
        step_results=[
            StepResultResponse(
                step_id=r.step_id,
                status=r.status.value,
                output=r.output,
                cost=r.cost,
                latency_ms=r.latency_ms,
            )
            for r in ex.step_results
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=WorkflowDetailResponse, status_code=201)
async def create_workflow(body: CreateWorkflowRequest) -> WorkflowDetailResponse:
    """Create a new workflow definition."""
    steps = [
        WorkflowStep(
            id=s.id,
            listing_id=s.listing_id,
            step_type=s.step_type,
            inputs=s.inputs,
            outputs=s.outputs,
            depends_on=s.depends_on,
        )
        for s in body.steps
    ]

    definition = WorkflowDefinition(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        steps=steps,
        owner=body.owner,
        created_at=utcnow(),
    )

    # Validate structure before persisting
    errors = validate_workflow(definition)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    _workflows[definition.id] = definition
    return _workflow_to_detail(definition)


@router.get("", response_model=WorkflowListResponse)
async def list_workflows() -> WorkflowListResponse:
    """List all saved workflow definitions."""
    items = [_workflow_to_response(wf) for wf in _workflows.values()]
    return WorkflowListResponse(workflows=items, count=len(items))


# Static sub-paths must be registered before the /{workflow_id} wildcard
# so FastAPI doesn't swallow "executions" or "validate" as a workflow ID.


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str) -> ExecutionResponse:
    """Get the status and results of a workflow execution."""
    ex = _executions.get(execution_id)
    if ex is None:
        raise HTTPException(
            status_code=404, detail=f"Execution {execution_id} not found"
        )
    return _execution_to_response(ex)


@router.post("/validate", response_model=ValidationResponse)
async def validate_workflow_endpoint(
    body: ValidateWorkflowRequest,
) -> ValidationResponse:
    """Validate a workflow definition without saving it."""
    steps = [
        WorkflowStep(
            id=s.id,
            listing_id=s.listing_id,
            step_type=s.step_type,
            inputs=s.inputs,
            outputs=s.outputs,
            depends_on=s.depends_on,
        )
        for s in body.steps
    ]

    definition = WorkflowDefinition(
        name=body.name,
        steps=steps,
    )

    errors = validate_workflow(definition)
    return ValidationResponse(valid=len(errors) == 0, errors=errors)


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(workflow_id: str) -> WorkflowDetailResponse:
    """Get a workflow definition by ID."""
    wf = _workflows.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    return _workflow_to_detail(wf)


@router.post("/{workflow_id}/execute", response_model=ExecutionResponse)
async def execute_workflow_endpoint(
    workflow_id: str,
    body: ExecuteWorkflowRequest | None = None,
) -> ExecutionResponse:
    """Trigger execution of a saved workflow."""
    wf = _workflows.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    inputs = body.inputs if body else {}

    try:
        # Structural validation only — skip registry listing checks so
        # workflows can run in local dev without pre-populating the store
        execution = await execute_workflow(wf, inputs)
    except WorkflowValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors)

    _executions[execution.id] = execution
    return _execution_to_response(execution)
