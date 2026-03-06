"""End-to-end routing evaluation via paired model comparison.

For each prompt, runs it through both the requested model and the routed model,
scores both with an LLM-as-judge, and measures the quality delta.
"""

from __future__ import annotations

import random
from typing import Any, Callable

from pydantic import BaseModel

from blockthrough.eval.types import model_cost
from blockthrough.routing.router import FitnessCache, resolve
from blockthrough.routing.types import RoutingPolicy


class PairedResult(BaseModel):
    """Quality comparison between requested and routed models for one prompt."""

    prompt_index: int
    task_type: str
    requested_model: str
    routed_model: str
    requested_score: float
    routed_score: float
    quality_delta: float  # routed - requested
    cost_requested: float
    cost_routed: float
    cost_delta: float  # routed - requested (negative = savings)
    was_overridden: bool


class ParetoPoint(BaseModel):
    """One point on the cost-quality frontier."""

    model: str
    avg_quality: float
    avg_cost: float
    is_frontier: bool


class E2EReport(BaseModel):
    """Summary of an end-to-end paired evaluation run."""

    total: int
    overridden_count: int
    avg_quality_delta: float
    quality_degradation_count: int  # cases where routed scored >0.05 worse
    total_cost_savings: float
    results: list[PairedResult]


# Type for judge function: (messages, model, task_type) -> score 0-1
JudgeFn = Callable[[list[dict[str, Any]], str, str], float]


async def run_paired_eval(
    prompts: list[dict[str, Any]],
    policy: RoutingPolicy,
    fitness_cache: FitnessCache,
    judge_fn: JudgeFn,
    *,
    sample_size: int | None = None,
    dry_run: bool = False,
) -> E2EReport:
    """Run paired evaluation: score both requested and routed model outputs.

    Each prompt dict must have: messages, task_type, requested_model.
    judge_fn(messages, model, task_type) -> score (0-1).
    """
    if sample_size is not None and sample_size < len(prompts):
        prompts = random.sample(prompts, sample_size)

    results: list[PairedResult] = []

    for i, prompt in enumerate(prompts):
        messages = prompt["messages"]
        task_type = prompt["task_type"]
        requested_model = prompt["requested_model"]

        decision = resolve(
            task_type, requested_model, fitness_cache, policy,
            has_tool_use=prompt.get("has_tool_use", False),
        )

        cost_req = model_cost(requested_model)
        cost_routed = model_cost(decision.selected_model)

        if dry_run:
            req_score = routed_score = 0.0
        else:
            req_score = judge_fn(messages, requested_model, task_type)
            if decision.was_overridden:
                routed_score = judge_fn(messages, decision.selected_model, task_type)
            else:
                routed_score = req_score

        results.append(PairedResult(
            prompt_index=i,
            task_type=task_type,
            requested_model=requested_model,
            routed_model=decision.selected_model,
            requested_score=req_score,
            routed_score=routed_score,
            quality_delta=routed_score - req_score,
            cost_requested=cost_req,
            cost_routed=cost_routed,
            cost_delta=cost_routed - cost_req,
            was_overridden=decision.was_overridden,
        ))

    overridden = [r for r in results if r.was_overridden]
    quality_deltas = [r.quality_delta for r in overridden] if overridden else [0.0]
    degraded = sum(1 for r in results if r.quality_delta < -0.05)

    return E2EReport(
        total=len(results),
        overridden_count=len(overridden),
        avg_quality_delta=sum(quality_deltas) / len(quality_deltas),
        quality_degradation_count=degraded,
        total_cost_savings=sum(r.cost_delta for r in results),
        results=results,
    )


def compute_pareto_frontier(results: list[PairedResult]) -> list[ParetoPoint]:
    """Compute the cost-quality Pareto frontier from paired results.

    Groups results by routed model, averages quality and cost,
    then marks non-dominated points as frontier.
    """
    # Group by model
    by_model: dict[str, list[PairedResult]] = {}
    for r in results:
        by_model.setdefault(r.routed_model, []).append(r)

    points: list[ParetoPoint] = []
    for model, model_results in by_model.items():
        avg_q = sum(r.routed_score for r in model_results) / len(model_results)
        avg_c = model_cost(model)
        points.append(ParetoPoint(
            model=model, avg_quality=avg_q, avg_cost=avg_c, is_frontier=False,
        ))

    # Mark Pareto-optimal: no other point has both higher quality AND lower cost
    for i, p in enumerate(points):
        dominated = False
        for j, other in enumerate(points):
            if i == j:
                continue
            if other.avg_quality >= p.avg_quality and other.avg_cost <= p.avg_cost:
                if other.avg_quality > p.avg_quality or other.avg_cost < p.avg_cost:
                    dominated = True
                    break
        points[i] = p.model_copy(update={"is_frontier": not dominated})

    return points
