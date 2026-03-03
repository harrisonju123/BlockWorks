"""Pydantic models for workflow definitions, executions, and results.

These types define the DAG structure for composable workflows that
chain agents and MCP tools from the registry, with cost tracking
and payment splitting.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class StepType(str, enum.Enum):
    AGENT = "agent"
    MCP_TOOL = "mcp_tool"


class WorkflowExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStep(BaseModel):
    """A single step in a workflow DAG.

    Each step references a registry listing (agent or MCP server) and
    declares its data dependencies via depends_on. The engine uses
    this to build a topological execution order.
    """

    id: str
    listing_id: str
    step_type: StepType
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """Complete workflow DAG with metadata.

    Immutable once created — new versions get a new ID. The steps
    list forms a DAG via depends_on references between step IDs.
    """

    id: str = ""
    name: str
    description: str = ""
    steps: list[WorkflowStep]
    owner: str = ""
    created_at: datetime | None = None
    version: int = 1


class StepResult(BaseModel):
    """Outcome of executing a single workflow step."""

    step_id: str
    status: WorkflowExecutionStatus
    output: dict[str, str] = Field(default_factory=dict)
    cost: float = 0.0
    latency_ms: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowExecution(BaseModel):
    """Tracks the state of a running or completed workflow."""

    id: str = ""
    workflow_id: str
    status: WorkflowExecutionStatus = WorkflowExecutionStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    steps_completed: int = 0
    total_cost: float = 0.0
    trace_id: str = ""
    step_results: list[StepResult] = Field(default_factory=list)


class PaymentSplit(BaseModel):
    """How much each step's listing earns from a workflow execution."""

    step_id: str
    listing_id: str
    amount: float
    percentage_of_total: float
