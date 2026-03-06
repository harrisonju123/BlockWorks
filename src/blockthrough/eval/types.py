"""Pydantic models for the routing evaluation framework."""

from __future__ import annotations

import enum

from pydantic import BaseModel

from blockthrough.models import MODEL_CATALOG


def model_cost(model: str) -> float:
    """Look up a model's avg cost from MODEL_CATALOG. Returns 0.0 for unknown models."""
    info = MODEL_CATALOG.get(model)
    return info.avg_cost if info is not None else 0.0


class ExpectedBehavior(str, enum.Enum):
    OVERRIDE = "override"
    PASSTHROUGH = "passthrough"


class RoutingExpectation(BaseModel):
    task_type: str
    requested_model: str
    expected_behavior: ExpectedBehavior
    expected_model: str | None = None
    has_tool_use: bool = False
    allowed_models: set[str] | None = None
    description: str = ""


class SimulationRow(BaseModel):
    expectation: RoutingExpectation
    decision_model: str
    decision_reason: str
    decision_was_overridden: bool
    behavior_match: bool
    model_match: bool | None = None
    cost_delta: float


class TaskBreakdown(BaseModel):
    override_count: int = 0
    passthrough_count: int = 0
    total: int = 0


class SimulationReport(BaseModel):
    total: int
    behavior_matches: int
    behavior_accuracy: float
    override_rate: float
    avg_cost_delta: float
    quality_risk_count: int
    per_task_breakdown: dict[str, TaskBreakdown]
    rows: list[SimulationRow]


class PolicyComparison(BaseModel):
    policy_a_report: SimulationReport
    policy_b_report: SimulationReport
    behavior_agreement_rate: float
    cost_delta_diff: float
    differing_decisions: list[tuple[SimulationRow, SimulationRow]]
