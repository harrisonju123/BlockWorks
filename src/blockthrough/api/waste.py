"""Waste score calculation — thin wrapper around the unified suggestion engine.

Determines what fraction of AI spend could be saved by routing tasks to
cheaper models. Uses fitness matrix data when provided (empirical),
falls back to tier-based heuristic with capped confidence when it doesn't.
"""

from __future__ import annotations

from blockthrough.api.schemas import WasteBreakdownItem, WasteScoreResponse
from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.models import get_tier
from blockthrough.waste.suggest import suggest_alternative


def compute_waste_score(
    rows: list[dict],
    fitness_entries: list[FitnessEntry] | None = None,
) -> WasteScoreResponse:
    """Turn raw (task_type, model) aggregates into a waste score.

    When fitness_entries are provided, suggestions are backed by benchmark
    data with evidence-based confidence. Without them, the tier-based
    heuristic fires with confidence capped at 0.5.
    """
    if not rows:
        return WasteScoreResponse(
            waste_score=0.0,
            total_potential_savings_usd=0.0,
            breakdown=[],
        )

    breakdown: list[WasteBreakdownItem] = []
    total_spend = 0.0
    total_savings = 0.0

    for row in rows:
        cost = float(row["total_cost"] or 0)
        total_spend += cost

        task_type_str = row["task_type"]
        model = row["model"]

        tier = get_tier(model)
        if tier is None:
            continue

        suggestion = suggest_alternative(
            task_type_str, model, fitness_entries,
        )
        if suggestion is None:
            continue

        projected = cost * suggestion.cost_ratio
        savings = cost - projected
        total_savings += savings

        breakdown.append(
            WasteBreakdownItem(
                task_type=task_type_str,
                current_model=model,
                suggested_model=suggestion.suggested_model,
                call_count=int(row["call_count"]),
                current_cost_usd=round(cost, 6),
                projected_cost_usd=round(projected, 6),
                savings_usd=round(savings, 6),
                confidence=suggestion.confidence,
                suggestion_source=suggestion.source,
                quality_score=suggestion.quality,
                sample_size=suggestion.sample_size,
            )
        )

    waste_score = min(total_savings / total_spend, 1.0) if total_spend > 0 else 0.0

    return WasteScoreResponse(
        waste_score=round(waste_score, 6),
        total_potential_savings_usd=round(total_savings, 6),
        breakdown=sorted(breakdown, key=lambda b: b.savings_usd, reverse=True),
    )
