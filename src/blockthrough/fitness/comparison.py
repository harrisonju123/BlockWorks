"""Head-to-head model comparison on a given task type.

Computes quality, cost, and latency deltas and generates a
human-readable recommendation string.
"""

from __future__ import annotations

from blockthrough.fitness.types import LeaderboardEntry, ModelComparison


class ComparisonError(Exception):
    """Raised when a comparison cannot be performed."""


def compare_models(
    model_a: str,
    model_b: str,
    task_type: str,
    leaderboard: list[LeaderboardEntry],
) -> ModelComparison:
    """Compare two models on a specific task type.

    Args:
        model_a: First model name.
        model_b: Second model name.
        task_type: The task type to compare on.
        leaderboard: Pre-built leaderboard entries.

    Raises:
        ComparisonError: If either model is not found for the task type.
    """
    entry_a = _find_entry(model_a, task_type, leaderboard)
    entry_b = _find_entry(model_b, task_type, leaderboard)

    if entry_a is None:
        raise ComparisonError(
            f"Model '{model_a}' not found for task type '{task_type}'"
        )
    if entry_b is None:
        raise ComparisonError(
            f"Model '{model_b}' not found for task type '{task_type}'"
        )

    quality_delta = entry_a.quality_score - entry_b.quality_score
    cost_delta = entry_a.cost_per_1k - entry_b.cost_per_1k
    latency_delta = entry_a.latency_ms - entry_b.latency_ms

    recommendation = _generate_recommendation(
        model_a, model_b, entry_a, entry_b,
        quality_delta, cost_delta,
    )

    return ModelComparison(
        model_a=model_a,
        model_b=model_b,
        task_type=task_type,
        quality_delta=round(quality_delta, 6),
        cost_delta=round(cost_delta, 6),
        latency_delta=round(latency_delta, 2),
        recommendation=recommendation,
    )


def _find_entry(
    model: str,
    task_type: str,
    leaderboard: list[LeaderboardEntry],
) -> LeaderboardEntry | None:
    for entry in leaderboard:
        if entry.model == model and entry.task_type == task_type:
            return entry
    return None


def _generate_recommendation(
    model_a: str,
    model_b: str,
    entry_a: LeaderboardEntry,
    entry_b: LeaderboardEntry,
    quality_delta: float,
    cost_delta: float,
) -> str:
    """Build a recommendation string based on quality and cost trade-offs."""
    # Percentage differences, guarding against division by zero
    quality_pct = (
        abs(quality_delta) / entry_b.quality_score * 100
        if entry_b.quality_score > 0
        else 0.0
    )
    cost_pct = (
        abs(cost_delta) / entry_b.cost_per_1k * 100
        if entry_b.cost_per_1k > 0
        else 0.0
    )

    # Threshold: < 2% quality difference counts as "comparable"
    quality_comparable = quality_pct < 2.0

    if quality_comparable:
        # Quality is roughly equal -- recommend the cheaper one
        if cost_delta < 0:
            return (
                f"{model_a} offers comparable quality at "
                f"{cost_pct:.0f}% lower cost"
            )
        elif cost_delta > 0:
            return (
                f"{model_b} offers comparable quality at "
                f"{cost_pct:.0f}% lower cost"
            )
        return f"{model_a} and {model_b} are equivalent on {entry_a.task_type}"

    if quality_delta > 0:
        # model_a is better quality
        if cost_delta <= 0:
            return (
                f"{model_a} is {quality_pct:.0f}% better quality "
                f"at {cost_pct:.0f}% lower cost"
            )
        return (
            f"{model_a} is {quality_pct:.0f}% better quality "
            f"at {cost_pct:.0f}% higher cost"
        )

    # model_b is better quality
    if cost_delta >= 0:
        return (
            f"{model_b} is {quality_pct:.0f}% better quality "
            f"at {cost_pct:.0f}% lower cost"
        )
    return (
        f"{model_b} is {quality_pct:.0f}% better quality "
        f"at {cost_pct:.0f}% higher cost"
    )
