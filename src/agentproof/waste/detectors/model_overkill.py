"""Model overkill detector (1B-1).

Compares actual usage against the fitness matrix to find cases where
an expensive model is used for a task where a cheaper model scores
above the quality threshold. Delegates model selection to the unified
suggestion engine so recommendations stay consistent across the
heuristic /waste-score and detailed /waste/details paths.
"""

from __future__ import annotations

from agentproof.models import MODEL_CATALOG
from agentproof.benchmarking.types import FitnessEntry
from agentproof.waste.suggest import suggest_alternative
from agentproof.waste.types import WasteCategory, WasteItem, WasteSeverity


def detect_model_overkill(
    usage_rows: list[dict],
    fitness_entries: list[FitnessEntry],
    *,
    quality_threshold: float = 0.90,
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

        suggestion = suggest_alternative(
            task_type, current_model, fitness_entries,
            quality_threshold=quality_threshold,
        )

        if suggestion is None or suggestion.source != "fitness":
            # Detector only uses empirical data — heuristic handled by waste scorer
            continue

        projected_cost = total_cost * suggestion.cost_ratio
        savings = total_cost - projected_cost

        if savings <= 0:
            continue

        severity = _classify_severity(savings)
        quality_str = f"{suggestion.quality:.0%}" if suggestion.quality else "?"

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
                    f"{current_model} to {suggestion.suggested_model} "
                    f"(quality: {quality_str})"
                ),
                confidence=suggestion.confidence,
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _classify_severity(savings: float) -> WasteSeverity:
    if savings >= 500:
        return WasteSeverity.CRITICAL
    if savings >= 50:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
