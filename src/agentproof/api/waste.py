"""Waste score calculation — heuristic v0.

Determines what fraction of AI spend could be saved by routing tasks to
cheaper models that are sufficient for the job.  The scoring rules here
are deliberately conservative; Phase 1's benchmarking engine will let us
tighten them with empirical data.
"""

from __future__ import annotations

from agentproof.api.schemas import WasteBreakdownItem, WasteScoreResponse
from agentproof.models import MODEL_CATALOG, ModelInfo, get_tier
from agentproof.types import TaskType

# Backward-compat aliases so downstream code that imports from here keeps working.
# New code should import directly from agentproof.models.
ModelCostInfo = ModelInfo
MODEL_COST_TIERS = MODEL_CATALOG

# Pre-built str -> TaskType lookup for O(1) conversion instead of enum constructor scan
_TASK_TYPE_MAP: dict[str, TaskType] = {t.value: t for t in TaskType}

# Quick lookup: tier number -> cheapest model name in that tier.
_CHEAPEST_BY_TIER: dict[int, str] = {}
for _model, _info in MODEL_CATALOG.items():
    if _info.tier not in _CHEAPEST_BY_TIER:
        _CHEAPEST_BY_TIER[_info.tier] = _model
    else:
        existing = MODEL_CATALOG[_CHEAPEST_BY_TIER[_info.tier]]
        if _info.avg_cost < existing.avg_cost:
            _CHEAPEST_BY_TIER[_info.tier] = _model

_SIMPLE_TASKS: set[TaskType] = {
    TaskType.CLASSIFICATION,
    TaskType.EXTRACTION,
    TaskType.CONVERSATION,
}


def _cost_ratio(current_model: str, suggested_model: str) -> float:
    cur = MODEL_CATALOG[current_model]
    sug = MODEL_CATALOG[suggested_model]
    if cur.avg_cost == 0:
        return 1.0
    return sug.avg_cost / cur.avg_cost


def _suggest_model(task_type: TaskType, current_tier: int) -> tuple[str | None, bool]:
    """Decide whether a (task_type, tier) combo is wasteful.

    Returns (suggested_model, is_flagged). None when not flagged.
    """
    if task_type in _SIMPLE_TASKS:
        if current_tier in (1, 2):
            return _CHEAPEST_BY_TIER[3], True
        return None, False

    if task_type == TaskType.SUMMARIZATION:
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[2], True
        return None, False

    if task_type in (TaskType.CODE_GENERATION, TaskType.REASONING):
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[2], True
        return None, False

    if task_type == TaskType.TOOL_SELECTION:
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[3], True
        return None, False

    return None, False


def compute_waste_score(rows: list[dict]) -> WasteScoreResponse:
    """Turn raw (task_type, model) aggregates into a waste score."""
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

        tt = _TASK_TYPE_MAP.get(task_type_str)
        if tt is None:
            continue

        suggested_model, flagged = _suggest_model(tt, tier)
        if not flagged or suggested_model is None:
            continue

        ratio = _cost_ratio(model, suggested_model)
        projected = cost * ratio
        savings = cost - projected
        total_savings += savings

        confidence = float(row["avg_confidence"]) if row.get("avg_confidence") else 0.5

        breakdown.append(
            WasteBreakdownItem(
                task_type=tt,
                current_model=model,
                suggested_model=suggested_model,
                call_count=int(row["call_count"]),
                current_cost_usd=round(cost, 6),
                projected_cost_usd=round(projected, 6),
                savings_usd=round(savings, 6),
                confidence=round(confidence, 4),
            )
        )

    waste_score = min(total_savings / total_spend, 1.0) if total_spend > 0 else 0.0

    return WasteScoreResponse(
        waste_score=round(waste_score, 6),
        total_potential_savings_usd=round(total_savings, 6),
        breakdown=sorted(breakdown, key=lambda b: b.savings_usd, reverse=True),
    )
