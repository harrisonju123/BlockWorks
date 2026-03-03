"""Embeddable widget and badge data generators.

Produces lightweight JSON payloads suitable for README badges,
blog embeds, and dashboard summary cards. No DB access -- operates
purely on the pre-built leaderboard.
"""

from __future__ import annotations

from agentproof.fitness.types import LeaderboardEntry


def generate_badge_data(
    model: str,
    task_type: str,
    leaderboard: list[LeaderboardEntry],
) -> dict:
    """Generate badge data for a specific model + task type.

    Returns a dict with rank, quality, cost, and sample_size that
    a frontend badge renderer (shields.io-style) can consume.
    Returns a "not found" payload when the model isn't on the board.
    """
    entry = _find_entry(model, task_type, leaderboard)
    if entry is None:
        return {
            "model": model,
            "task_type": task_type,
            "status": "not_ranked",
            "message": f"{model} has no data for {task_type}",
        }

    return {
        "model": entry.model,
        "task_type": entry.task_type,
        "rank": entry.rank,
        "quality_score": round(entry.quality_score, 4),
        "cost_per_1k": round(entry.cost_per_1k, 6),
        "latency_ms": round(entry.latency_ms, 1),
        "sample_size": entry.sample_size,
        "verified": entry.verified,
        "status": "ranked",
    }


def generate_summary_widget(
    leaderboard: list[LeaderboardEntry],
) -> dict:
    """Generate a summary widget showing the best model per task type.

    Returns a dict with:
    - top_models: dict mapping task_type -> best model info
    - total_models: distinct model count on the board
    - total_task_types: distinct task type count
    - total_benchmarks: sum of all sample sizes
    """
    top_models: dict[str, dict] = {}
    all_models: set[str] = set()
    all_task_types: set[str] = set()
    total_benchmarks = 0

    for entry in leaderboard:
        all_models.add(entry.model)
        all_task_types.add(entry.task_type)
        total_benchmarks += entry.sample_size

        # Rank 1 = best for that task type
        if entry.rank == 1:
            top_models[entry.task_type] = {
                "model": entry.model,
                "quality_score": round(entry.quality_score, 4),
                "cost_per_1k": round(entry.cost_per_1k, 6),
                "verified": entry.verified,
            }

    return {
        "top_models": top_models,
        "total_models": len(all_models),
        "total_task_types": len(all_task_types),
        "total_benchmarks": total_benchmarks,
    }


def _find_entry(
    model: str,
    task_type: str,
    leaderboard: list[LeaderboardEntry],
) -> LeaderboardEntry | None:
    for entry in leaderboard:
        if entry.model == model and entry.task_type == task_type:
            return entry
    return None
