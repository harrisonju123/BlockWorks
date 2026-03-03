"""Public fitness index endpoints: leaderboard, comparison, trends, summary.

These endpoints serve the aggregated, anonymized "public good" view of
model performance. No org_id filtering -- this is the global leaderboard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db
from agentproof.db.queries import get_fitness_matrix
from agentproof.fitness.builder import build_leaderboard
from agentproof.fitness.comparison import ComparisonError, compare_models
from agentproof.fitness.trends import compute_trend_slope, get_trends
from agentproof.fitness.types import (
    LeaderboardEntry,
    LeaderboardFilter,
    ModelComparison,
    TrendPoint,
)
from agentproof.fitness.widget import generate_badge_data, generate_summary_widget

router = APIRouter(prefix="/fitness")


# -- Response schemas ----------------------------------------------------------


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    total_models: int
    total_entries: int


class CompareResponse(BaseModel):
    comparison: ModelComparison


class CompareErrorResponse(BaseModel):
    detail: str


class TrendResponse(BaseModel):
    model: str
    task_type: str | None
    points: list[TrendPoint]
    slope: float
    direction: str


class SummaryResponse(BaseModel):
    top_models: dict[str, dict]
    total_models: int
    total_task_types: int
    total_benchmarks: int


class BadgeResponse(BaseModel):
    badge: dict


# -- Endpoints -----------------------------------------------------------------


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def leaderboard(
    task_type: str | None = Query(default=None),
    min_sample_size: int = Query(default=10, ge=1),
    verified_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> LeaderboardResponse:
    """Full leaderboard, filterable by task_type and verification status."""
    entries = await get_fitness_matrix(db)
    board = build_leaderboard(entries)

    # Apply filters on the built leaderboard
    filtered = board
    if task_type:
        filtered = [e for e in filtered if e.task_type == task_type]
    if min_sample_size > 0:
        filtered = [e for e in filtered if e.sample_size >= min_sample_size]
    if verified_only:
        filtered = [e for e in filtered if e.verified]

    models = {e.model for e in filtered}
    return LeaderboardResponse(
        entries=filtered,
        total_models=len(models),
        total_entries=len(filtered),
    )


@router.get("/compare", response_model=CompareResponse | CompareErrorResponse)
async def compare(
    model_a: str = Query(...),
    model_b: str = Query(...),
    task_type: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> CompareResponse | CompareErrorResponse:
    """Compare two models head-to-head on a task type."""
    entries = await get_fitness_matrix(db)
    board = build_leaderboard(entries)

    try:
        comparison = compare_models(model_a, model_b, task_type, board)
    except ComparisonError as exc:
        return CompareErrorResponse(detail=str(exc))

    return CompareResponse(comparison=comparison)


@router.get("/trends/{model}", response_model=TrendResponse)
async def trends(
    model: str,
    task_type: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> TrendResponse:
    """Performance trends for a model over time."""
    points = await get_trends(db, model, task_type=task_type, days=days)
    slope = compute_trend_slope(points)

    if slope > 0.001:
        direction = "improving"
    elif slope < -0.001:
        direction = "degrading"
    else:
        direction = "stable"

    return TrendResponse(
        model=model,
        task_type=task_type,
        points=points,
        slope=slope,
        direction=direction,
    )


@router.get("/summary", response_model=SummaryResponse)
async def summary(
    db: AsyncSession = Depends(get_db),
) -> SummaryResponse:
    """High-level summary: best model per task type, totals."""
    entries = await get_fitness_matrix(db)
    board = build_leaderboard(entries)
    widget = generate_summary_widget(board)

    return SummaryResponse(
        top_models=widget["top_models"],
        total_models=widget["total_models"],
        total_task_types=widget["total_task_types"],
        total_benchmarks=widget["total_benchmarks"],
    )


@router.get("/badge", response_model=BadgeResponse)
async def badge(
    model: str = Query(...),
    task_type: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> BadgeResponse:
    """Badge data for embedding in READMEs and blogs."""
    entries = await get_fitness_matrix(db)
    board = build_leaderboard(entries)
    data = generate_badge_data(model, task_type, board)

    return BadgeResponse(badge=data)
