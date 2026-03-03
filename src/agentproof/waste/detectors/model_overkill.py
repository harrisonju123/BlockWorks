"""Model overkill detector (1B-1).

Compares actual usage against the fitness matrix to find cases where
an expensive model is used for a task where a cheaper model scores
above the quality threshold. Dollar amounts come from real benchmark
data rather than the v0 heuristic rules.
"""

from __future__ import annotations

from agentproof.models import MODEL_CATALOG, ModelInfo
from agentproof.benchmarking.types import FitnessEntry
from agentproof.waste.types import WasteCategory, WasteItem, WasteSeverity

# Minimum quality threshold — a cheaper model must score above this
# to be considered a viable replacement.
_QUALITY_THRESHOLD = 0.90


def detect_model_overkill(
    usage_rows: list[dict],
    fitness_entries: list[FitnessEntry],
    *,
    quality_threshold: float = _QUALITY_THRESHOLD,
) -> list[WasteItem]:
    """Find (task_type, model) pairs where a cheaper model would suffice.

    Args:
        usage_rows: Output of get_waste_analysis — per (task_type, model) aggregates
            with keys: task_type, model, call_count, total_cost.
        fitness_entries: Output of get_fitness_matrix — benchmark quality/cost data.
        quality_threshold: Minimum quality score for the cheaper alternative.

    Returns:
        WasteItems for each overspend found.
    """
    if not usage_rows or not fitness_entries:
        return []

    # Build lookup: (task_type, model) -> FitnessEntry for quick comparison
    fitness_by_task: dict[str, list[FitnessEntry]] = {}
    for entry in fitness_entries:
        fitness_by_task.setdefault(entry.task_type, []).append(entry)

    items: list[WasteItem] = []

    for row in usage_rows:
        task_type = row.get("task_type")
        current_model = row.get("model")
        total_cost = float(row.get("total_cost") or 0)
        call_count = int(row.get("call_count") or 0)

        if not task_type or not current_model or total_cost <= 0:
            continue

        current_cost_info = MODEL_CATALOG.get(current_model)
        if not current_cost_info:
            continue

        # Look for a cheaper model that meets the quality bar
        candidates = fitness_by_task.get(task_type, [])
        best_alternative = _find_cheapest_qualified(
            candidates, current_cost_info, quality_threshold
        )

        if best_alternative is None:
            continue

        alt_cost_info = MODEL_CATALOG.get(best_alternative.model)
        if not alt_cost_info:
            continue

        # Project savings based on cost ratio
        if current_cost_info.avg_cost == 0:
            continue
        cost_ratio = alt_cost_info.avg_cost / current_cost_info.avg_cost
        projected_cost = total_cost * cost_ratio
        savings = total_cost - projected_cost

        if savings <= 0:
            continue

        severity = _classify_severity(savings)

        items.append(
            WasteItem(
                category=WasteCategory.MODEL_OVERKILL,
                severity=severity,
                call_count=call_count,
                current_cost=round(total_cost, 6),
                projected_cost=round(projected_cost, 6),
                savings=round(savings, 6),
                description=(
                    f"Switch {call_count:,} {task_type} calls from "
                    f"{current_model} to {best_alternative.model} "
                    f"(quality: {best_alternative.avg_quality:.0%})"
                ),
                confidence=round(min(best_alternative.avg_quality, 1.0), 4),
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _find_cheapest_qualified(
    candidates: list[FitnessEntry],
    current_cost: ModelInfo,
    quality_threshold: float,
) -> FitnessEntry | None:
    """Pick the cheapest model that exceeds the quality bar and is cheaper than current."""
    best: FitnessEntry | None = None
    best_avg_cost: float = current_cost.avg_cost

    for entry in candidates:
        if entry.avg_quality < quality_threshold:
            continue

        alt_cost_info = MODEL_CATALOG.get(entry.model)
        if not alt_cost_info:
            continue

        # Must actually be cheaper
        if alt_cost_info.avg_cost >= current_cost.avg_cost:
            continue

        if alt_cost_info.avg_cost < best_avg_cost:
            best = entry
            best_avg_cost = alt_cost_info.avg_cost

    return best


def _classify_severity(savings: float) -> WasteSeverity:
    if savings >= 500:
        return WasteSeverity.CRITICAL
    if savings >= 50:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
