"""Dry-run simulator -- "what would have happened under this policy?"

Queries historical llm_events, replays each through the router engine,
and produces a report comparing actual vs. hypothetical model selections.
This is the trust-building feature: operators can preview routing impact
before enabling a policy.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.models import MODEL_CATALOG
from agentproof.routing.router import FitnessCache, resolve
from agentproof.routing.types import RoutingDecision, RoutingPolicy


class DryRunEvent(BaseModel):
    """One historical event and what the router would have decided."""

    trace_id: str
    original_model: str
    original_cost: float
    task_type: str | None
    decision: RoutingDecision


class ModelDistribution(BaseModel):
    """How traffic would shift between models."""

    model: str
    original_count: int
    routed_count: int
    original_cost: float
    routed_cost: float


class DryRunReport(BaseModel):
    """Aggregated results of a dry-run simulation."""

    total_events: int
    events_affected: int  # where selected_model != original_model
    original_total_cost: float
    projected_total_cost: float
    cost_savings: float
    savings_pct: float
    model_distribution: list[ModelDistribution]
    sample_decisions: list[DryRunEvent]  # first N decisions for inspection


async def _fetch_historical_events(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    limit: int = 10_000,
) -> list[dict]:
    """Pull historical events from llm_events for dry-run replay."""
    query = text("""
        SELECT
            trace_id,
            model,
            estimated_cost,
            task_type,
            latency_ms
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
          AND task_type IS NOT NULL
        ORDER BY created_at
        LIMIT :limit
    """)
    result = await session.execute(
        query, {"start": start, "end": end, "limit": limit}
    )
    return [dict(row._mapping) for row in result.fetchall()]


def _estimate_routed_cost(
    original_model: str,
    original_cost: float,
    routed_model: str,
) -> float:
    """Estimate what the cost would have been with the routed model.

    Uses the ratio of avg costs from MODEL_CATALOG. If either model
    is unknown, assume the same cost (conservative).
    """
    orig_info = MODEL_CATALOG.get(original_model)
    routed_info = MODEL_CATALOG.get(routed_model)

    if orig_info is None or routed_info is None or orig_info.avg_cost == 0:
        return original_cost

    ratio = routed_info.avg_cost / orig_info.avg_cost
    return original_cost * ratio


async def dry_run(
    policy: RoutingPolicy,
    start: datetime,
    end: datetime,
    session: AsyncSession,
    fitness_cache: FitnessCache,
    sample_limit: int = 20,
) -> DryRunReport:
    """Simulate routing decisions against historical data.

    For each historical event in the time window, compute what the
    router would have decided under the given policy. Aggregate into
    a report showing cost savings, quality impact, and model distribution.
    """
    events = await _fetch_historical_events(session, start, end)

    decisions: list[DryRunEvent] = []
    original_total_cost = 0.0
    projected_total_cost = 0.0
    events_affected = 0

    # Track model distribution shifts
    original_counts: dict[str, int] = {}
    routed_counts: dict[str, int] = {}
    original_costs: dict[str, float] = {}
    routed_costs: dict[str, float] = {}

    for event in events:
        task_type = event["task_type"] or "unknown"
        original_model = event["model"]
        original_cost = float(event["estimated_cost"] or 0)

        decision = resolve(
            task_type=task_type,
            requested_model=original_model,
            fitness_cache=fitness_cache,
            policy=policy,
        )

        routed_cost = _estimate_routed_cost(
            original_model, original_cost, decision.selected_model
        )

        original_total_cost += original_cost
        projected_total_cost += routed_cost

        if decision.was_overridden:
            events_affected += 1

        # Track distributions
        original_counts[original_model] = original_counts.get(original_model, 0) + 1
        routed_counts[decision.selected_model] = routed_counts.get(decision.selected_model, 0) + 1
        original_costs[original_model] = original_costs.get(original_model, 0) + original_cost
        routed_costs[decision.selected_model] = (
            routed_costs.get(decision.selected_model, 0) + routed_cost
        )

        decisions.append(
            DryRunEvent(
                trace_id=event["trace_id"],
                original_model=original_model,
                original_cost=original_cost,
                task_type=task_type,
                decision=decision,
            )
        )

    # Build model distribution summary
    all_models = set(original_counts.keys()) | set(routed_counts.keys())
    distribution = [
        ModelDistribution(
            model=model,
            original_count=original_counts.get(model, 0),
            routed_count=routed_counts.get(model, 0),
            original_cost=round(original_costs.get(model, 0), 6),
            routed_cost=round(routed_costs.get(model, 0), 6),
        )
        for model in sorted(all_models)
    ]

    cost_savings = original_total_cost - projected_total_cost
    savings_pct = (cost_savings / original_total_cost * 100) if original_total_cost > 0 else 0.0

    return DryRunReport(
        total_events=len(events),
        events_affected=events_affected,
        original_total_cost=round(original_total_cost, 6),
        projected_total_cost=round(projected_total_cost, 6),
        cost_savings=round(cost_savings, 6),
        savings_pct=round(savings_pct, 2),
        model_distribution=distribution,
        sample_decisions=decisions[:sample_limit],
    )
