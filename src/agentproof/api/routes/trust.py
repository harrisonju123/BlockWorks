"""Trust score API endpoints — query and update agent reputation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, Query

from agentproof.trust.registry import AgentNotRegisteredError, TrustRegistry
from agentproof.trust.types import TrustDimension, TrustScore, TrustWeights

router = APIRouter()

# ---------------------------------------------------------------------------
# Singleton registry — replaced by DI in production
# ---------------------------------------------------------------------------

_registry = TrustRegistry()


def get_registry() -> TrustRegistry:
    return _registry


def reset_registry() -> None:
    """Reset the singleton registry. Used by tests."""
    _registry.reset()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TrustScoreResponse(BaseModel):
    agent_id: str
    reliability: float
    efficiency: float
    quality: float
    usage_volume: float
    composite_score: float
    last_updated: str


class TrustUpdateRequest(BaseModel):
    dimension: TrustDimension
    value: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class TopAgentEntry(BaseModel):
    agent_id: str
    composite_score: float


class TopAgentsResponse(BaseModel):
    agents: list[TopAgentEntry]
    total_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_to_response(s: TrustScore) -> TrustScoreResponse:
    return TrustScoreResponse(
        agent_id=s.agent_id,
        reliability=s.reliability,
        efficiency=s.efficiency,
        quality=s.quality,
        usage_volume=s.usage_volume,
        composite_score=s.composite_score,
        last_updated=s.last_updated.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Static paths must be declared before parameterized paths so FastAPI
# matches "/trust/top" before falling into "/trust/{agent_id}".


@router.get(
    "/trust/top",
    response_model=TopAgentsResponse,
)
async def get_top_agents(
    limit: int = Query(default=10, ge=1, le=100),
) -> TopAgentsResponse:
    registry = get_registry()
    top = registry.get_top_agents(limit=limit)
    return TopAgentsResponse(
        agents=[
            TopAgentEntry(
                agent_id=s.agent_id,
                composite_score=s.composite_score,
            )
            for s in top
        ],
        total_count=registry.agent_count(),
    )


@router.get(
    "/trust/{agent_id}",
    response_model=TrustScoreResponse,
)
async def get_trust_score(agent_id: str) -> TrustScoreResponse:
    registry = get_registry()
    try:
        score = registry.get_score(agent_id)
    except AgentNotRegisteredError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return _score_to_response(score)


@router.post(
    "/trust/{agent_id}/update",
    response_model=TrustScoreResponse,
)
async def update_trust_score(
    agent_id: str,
    body: TrustUpdateRequest,
) -> TrustScoreResponse:
    registry = get_registry()
    try:
        updated = registry.update_score(
            agent_id=agent_id,
            dimension=body.dimension,
            value=body.value,
            reason=body.reason,
        )
    except AgentNotRegisteredError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return _score_to_response(updated)
