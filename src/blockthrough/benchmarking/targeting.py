"""Smart benchmark targeting — dynamic model selection based on coverage gaps.

Replaces the hardcoded benchmark_models list with data-driven selection.
Identifies which cheaper models lack sufficient benchmark data for the
task types actually in use, ranked by potential cost savings.
"""

from __future__ import annotations

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.models import MODEL_CATALOG, ModelInfo


def compute_benchmark_targets(
    usage_rows: list[dict],
    fitness_entries: list[FitnessEntry],
    catalog: dict[str, ModelInfo] | None = None,
    *,
    max_targets: int = 6,
    min_sample_size: int = 10,
) -> list[str]:
    """Pick benchmark models that fill coverage gaps with the most savings potential.

    Args:
        usage_rows: Per (task_type, model) aggregates from get_waste_analysis.
            Keys: task_type, model, call_count, total_cost.
        fitness_entries: Current fitness matrix from DB.
        catalog: Model catalog to use (defaults to MODEL_CATALOG).
        max_targets: Maximum number of models to return.
        min_sample_size: Models with >= this many samples are considered
            well-characterized and skipped.

    Returns:
        Model names to benchmark, ordered by potential savings.
    """
    if catalog is None:
        catalog = MODEL_CATALOG

    if not usage_rows:
        return []

    # Build fitness lookup: (task_type, model) -> sample_size
    fitness_samples: dict[tuple[str, str], int] = {}
    for entry in fitness_entries:
        fitness_samples[(entry.task_type, entry.model)] = entry.sample_size

    # Find models in use and their costs
    in_use: dict[str, _UsageInfo] = {}
    for row in usage_rows:
        model = row.get("model")
        if not model or model not in catalog:
            continue
        cost = float(row.get("total_cost") or 0)
        count = int(row.get("call_count") or 0)
        task_type = row.get("task_type")
        if not task_type:
            continue

        if model not in in_use:
            in_use[model] = _UsageInfo(
                model=model,
                tier=catalog[model].tier,
                avg_cost=catalog[model].avg_cost,
                total_cost=0.0,
                call_count=0,
                task_types=set(),
            )
        info = in_use[model]
        info.total_cost += cost
        info.call_count += count
        info.task_types.add(task_type)

    if not in_use:
        return []

    # For each in-use model, find candidate cheaper models at same or lower tier
    # that lack sufficient benchmark coverage
    candidate_scores: dict[str, float] = {}

    for used_model, usage_info in in_use.items():
        used_cost = usage_info.avg_cost
        used_tier = usage_info.tier

        for cand_name, cand_info in catalog.items():
            if cand_name == used_model:
                continue
            # Candidates: same tier or cheaper (higher tier number)
            if cand_info.tier < used_tier:
                continue
            if cand_info.avg_cost >= used_cost:
                continue

            # Check if already well-characterized for all relevant task types
            all_covered = True
            for tt in usage_info.task_types:
                samples = fitness_samples.get((tt, cand_name), 0)
                if samples < min_sample_size:
                    all_covered = False
                    break

            if all_covered:
                continue

            # Score by potential savings: cost_delta * call_count
            savings_potential = (used_cost - cand_info.avg_cost) * usage_info.call_count
            # Accumulate across all in-use models this candidate could replace
            candidate_scores[cand_name] = (
                candidate_scores.get(cand_name, 0.0) + savings_potential
            )

    # Rank by savings potential, return top N
    ranked = sorted(candidate_scores.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, _score in ranked[:max_targets]]


class _UsageInfo:
    """Mutable accumulator for per-model usage stats."""

    __slots__ = ("model", "tier", "avg_cost", "total_cost", "call_count", "task_types")

    def __init__(
        self,
        model: str,
        tier: int,
        avg_cost: float,
        total_cost: float,
        call_count: int,
        task_types: set[str],
    ) -> None:
        self.model = model
        self.tier = tier
        self.avg_cost = avg_cost
        self.total_cost = total_cost
        self.call_count = call_count
        self.task_types = task_types
