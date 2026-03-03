"""Routing engine API endpoints.

Exposes policy management, dry-run simulation, routing decisions,
and A/B test configuration. The routing engine provides decisions
only -- it does not modify LiteLLM behavior directly.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db, resolve_time_range
from agentproof.routing.ab_test import ABTestConfig, assign_group, get_policy
from agentproof.routing.dry_run import DryRunReport, dry_run
from agentproof.routing.policy import (
    PolicyValidationError,
    default_policy,
    load_policy,
    validate_policy,
)
from agentproof.routing.router import FitnessCache, resolve
from agentproof.routing.types import RoutingDecision, RoutingPolicy

router = APIRouter(prefix="/routing")


# -- In-memory state (replaced by DB persistence in a future PR) ---------------
# Guarded by _state_lock. Not safe across multiple Uvicorn workers —
# DB persistence required before horizontal scaling.

_state_lock = asyncio.Lock()
_active_policy: RoutingPolicy | None = None
_fitness_cache = FitnessCache()
_ab_test_config: ABTestConfig | None = None
_recent_decisions: deque[RoutingDecision] = deque(maxlen=200)


def _get_active_policy() -> RoutingPolicy:
    global _active_policy
    if _active_policy is None:
        _active_policy = default_policy()
    return _active_policy


# -- Request/Response schemas --------------------------------------------------


class PolicyUpdateRequest(BaseModel):
    rules: list[dict] = Field(default_factory=list)
    version: int = 1


class PolicyResponse(BaseModel):
    policy: RoutingPolicy
    is_default: bool


class DryRunRequest(BaseModel):
    policy: dict | None = None  # If None, uses the active policy
    start: datetime | None = None
    end: datetime | None = None


class DecisionsResponse(BaseModel):
    decisions: list[RoutingDecision]
    total_count: int


class ABTestRequest(BaseModel):
    policy_a: dict | None = None  # If None, uses current active policy
    policy_b: dict
    split_ratio: float = Field(ge=0.0, le=1.0, default=0.5)
    enabled: bool = True


class ABTestResultsResponse(BaseModel):
    config: ABTestConfig
    control_decisions: int
    experiment_decisions: int


# -- Endpoints -----------------------------------------------------------------


@router.get("/policy", response_model=PolicyResponse)
async def get_active_policy() -> PolicyResponse:
    """Return the current active routing policy."""
    policy = _get_active_policy()
    return PolicyResponse(
        policy=policy,
        is_default=len(policy.rules) == 0,
    )


@router.post("/policy", response_model=PolicyResponse)
async def update_policy(request: PolicyUpdateRequest) -> PolicyResponse:
    """Update the active routing policy. Validates before applying."""
    global _active_policy

    try:
        policy = load_policy(request.model_dump())
    except PolicyValidationError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    _active_policy = policy
    return PolicyResponse(
        policy=policy,
        is_default=len(policy.rules) == 0,
    )


@router.post("/dry-run", response_model=DryRunReport)
async def run_dry_run(
    request: DryRunRequest,
    db: AsyncSession = Depends(get_db),
) -> DryRunReport:
    """Simulate routing decisions against historical data."""
    start, end = resolve_time_range(request.start, request.end, default_hours=168)

    if request.policy is not None:
        try:
            policy = load_policy(request.policy)
        except PolicyValidationError as e:
            raise HTTPException(status_code=422, detail={"errors": e.errors})
    else:
        policy = _get_active_policy()

    report = await dry_run(
        policy=policy,
        start=start,
        end=end,
        session=db,
        fitness_cache=_fitness_cache,
    )
    return report


@router.get("/decisions", response_model=DecisionsResponse)
async def get_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DecisionsResponse:
    """Return recent routing decisions with stats."""
    total = len(_recent_decisions)
    page = _recent_decisions[offset : offset + limit]
    return DecisionsResponse(decisions=page, total_count=total)


@router.post("/ab-test", response_model=ABTestResultsResponse)
async def configure_ab_test(request: ABTestRequest) -> ABTestResultsResponse:
    """Configure an A/B test between two routing policies."""
    global _ab_test_config

    try:
        policy_a = (
            load_policy(request.policy_a)
            if request.policy_a is not None
            else _get_active_policy()
        )
        policy_b = load_policy(request.policy_b)
    except PolicyValidationError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors})

    _ab_test_config = ABTestConfig(
        policy_a=policy_a,
        policy_b=policy_b,
        split_ratio=request.split_ratio,
        enabled=request.enabled,
    )

    return ABTestResultsResponse(
        config=_ab_test_config,
        control_decisions=0,
        experiment_decisions=0,
    )


@router.get("/ab-test/results", response_model=ABTestResultsResponse)
async def get_ab_test_results() -> ABTestResultsResponse:
    """Return A/B test configuration and decision counts."""
    if _ab_test_config is None:
        raise HTTPException(status_code=404, detail="No A/B test configured")

    # Count decisions by group from recent decisions
    control_count = sum(1 for d in _recent_decisions if d.group == "control")
    experiment_count = sum(1 for d in _recent_decisions if d.group == "experiment")

    return ABTestResultsResponse(
        config=_ab_test_config,
        control_decisions=control_count,
        experiment_decisions=experiment_count,
    )


def record_decision(decision: RoutingDecision) -> None:
    """Append a routing decision to the recent decisions buffer.

    Called by the routing integration layer (future PR) after each
    resolve() call. Trims to _MAX_RECENT_DECISIONS to bound memory.
    """
    _recent_decisions.append(decision)
    if len(_recent_decisions) > _MAX_RECENT_DECISIONS:
        del _recent_decisions[: len(_recent_decisions) - _MAX_RECENT_DECISIONS]
