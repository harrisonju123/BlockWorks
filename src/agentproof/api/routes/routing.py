"""Routing engine API endpoints.

Exposes policy management, dry-run simulation, routing decisions,
and A/B test configuration. The routing engine provides decisions
only -- it does not modify LiteLLM behavior directly.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db, resolve_time_range
from agentproof.db.queries import (
    get_active_routing_policy,
    get_routing_decisions,
    upsert_routing_policy,
)
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
from agentproof.routing.writer import DecisionRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing")


# -- In-memory state -----------------------------------------------------------
# The deque acts as a fast recent-decisions buffer for the proxy hot path.
# DB persistence happens via RoutingDecisionWriter in the background.
# Not safe across multiple Uvicorn workers —
# DB persistence required before horizontal scaling.
# Policy and FitnessCache live on app.state (set by lifespan in app.py) so the
# proxy and these API endpoints share the same objects.

_ab_test_config: ABTestConfig | None = None
_recent_decisions: deque[RoutingDecision] = deque(maxlen=200)


def _load_policy_from_row(row: dict) -> RoutingPolicy:
    """Deserialize a routing_policies DB row into a RoutingPolicy."""
    policy_data = row["policy_json"]
    if isinstance(policy_data, str):
        policy_data = json.loads(policy_data)
    return load_policy(policy_data)


async def _get_active_policy(request: Request) -> RoutingPolicy:
    """Return the routing policy from app.state, falling back to default.

    The lifespan loads the policy from DB at startup and POST /policy keeps it
    in sync — so reads never need to hit the DB. This avoids unnecessary latency
    and side-effects on GET requests.
    """
    policy = getattr(request.app.state, "routing_policy", None)
    if policy is None:
        policy = default_policy()
        request.app.state.routing_policy = policy
    return policy


def _get_fitness_cache(request: Request) -> FitnessCache:
    """Return the FitnessCache from app.state, creating if absent."""
    cache = getattr(request.app.state, "fitness_cache", None)
    if cache is None:
        cache = FitnessCache()
        request.app.state.fitness_cache = cache
    return cache


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
    decisions: list[dict]
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
async def get_active_policy_endpoint(
    request: Request,
) -> PolicyResponse:
    """Return the current active routing policy."""
    policy = await _get_active_policy(request)
    return PolicyResponse(
        policy=policy,
        is_default=len(policy.rules) == 0,
    )


@router.post("/policy", response_model=PolicyResponse)
async def update_policy(
    body: PolicyUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PolicyResponse:
    """Update the active routing policy. Validates, persists to DB, then applies in-memory."""
    try:
        policy = load_policy(body.model_dump())
    except PolicyValidationError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Persist to DB so it survives restarts
    try:
        await upsert_routing_policy(db, body.model_dump(), body.version)
    except Exception:
        logger.exception("Failed to persist routing policy to DB")
        # Still apply in-memory so the current process uses the new policy

    request.app.state.routing_policy = policy
    return PolicyResponse(
        policy=policy,
        is_default=len(policy.rules) == 0,
    )


@router.post("/dry-run", response_model=DryRunReport)
async def run_dry_run(
    body: DryRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DryRunReport:
    """Simulate routing decisions against historical data."""
    start, end = resolve_time_range(body.start, body.end, default_hours=168)

    if body.policy is not None:
        try:
            policy = load_policy(body.policy)
        except PolicyValidationError as e:
            raise HTTPException(status_code=422, detail={"errors": e.errors})
    else:
        policy = await _get_active_policy(request)

    report = await dry_run(
        policy=policy,
        start=start,
        end=end,
        session=db,
        fitness_cache=_get_fitness_cache(request),
    )
    return report


@router.get("/decisions", response_model=DecisionsResponse)
async def get_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DecisionsResponse:
    """Return routing decisions from the DB, falling back to in-memory deque."""
    try:
        rows, total = await get_routing_decisions(db, limit=limit, offset=offset)
        return DecisionsResponse(decisions=rows, total_count=total)
    except Exception:
        logger.warning("Failed to read routing decisions from DB, using in-memory buffer")

    # Fallback: serve from the in-memory deque
    decisions_list = [d.model_dump() for d in _recent_decisions]
    total = len(decisions_list)
    page = decisions_list[offset: offset + limit]
    return DecisionsResponse(decisions=page, total_count=total)


@router.post("/ab-test", response_model=ABTestResultsResponse)
async def configure_ab_test(body: ABTestRequest, request: Request) -> ABTestResultsResponse:
    """Configure an A/B test between two routing policies."""
    global _ab_test_config

    try:
        policy_a = (
            load_policy(body.policy_a)
            if body.policy_a is not None
            else await _get_active_policy(request)
        )
        policy_b = load_policy(body.policy_b)
    except PolicyValidationError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors})

    _ab_test_config = ABTestConfig(
        policy_a=policy_a,
        policy_b=policy_b,
        split_ratio=body.split_ratio,
        enabled=body.enabled,
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

    Called by the routing integration layer after each resolve() call.
    The deque's maxlen=200 automatically evicts oldest entries.
    """
    _recent_decisions.append(decision)
