"""Leaderboard construction from raw fitness entries.

Ranks models per task_type by quality (descending), using cost as a
tiebreaker (lower cost wins). Entries below the minimum sample size
are excluded. Verified status is set based on validator consensus data.
"""

from __future__ import annotations

from agentproof.benchmarking.types import FitnessEntry
from agentproof.fitness.types import FitnessIndexConfig, LeaderboardEntry


def build_leaderboard(
    fitness_entries: list[FitnessEntry],
    verified_tasks: set[tuple[str, str]] | None = None,
    config: FitnessIndexConfig | None = None,
) -> list[LeaderboardEntry]:
    """Build a ranked leaderboard from fitness matrix entries.

    Args:
        fitness_entries: Raw (model, task_type) aggregates from the DB.
        verified_tasks: Set of (model, task_type) pairs that have been
            validated by the decentralized consensus engine. None means
            no verification data available.
        config: Tuning knobs; uses defaults if not provided.

    Returns:
        Ranked LeaderboardEntry list, grouped by task_type.
    """
    cfg = config or FitnessIndexConfig()
    verified = verified_tasks or set()

    # Filter out entries below the sample size floor
    eligible = [e for e in fitness_entries if e.sample_size >= cfg.min_sample_size]

    # Group by task_type, then sort within each group:
    # primary: quality descending, secondary: cost ascending (cheaper wins ties)
    by_task: dict[str, list[FitnessEntry]] = {}
    for entry in eligible:
        by_task.setdefault(entry.task_type, []).append(entry)

    result: list[LeaderboardEntry] = []

    for task_type in sorted(by_task):
        entries = by_task[task_type]
        entries.sort(key=lambda e: (-e.avg_quality, e.avg_cost))

        for rank, entry in enumerate(entries, start=1):
            result.append(
                LeaderboardEntry(
                    model=entry.model,
                    task_type=entry.task_type,
                    quality_score=entry.avg_quality,
                    cost_per_1k=entry.avg_cost * 1000,
                    latency_ms=entry.avg_latency,
                    sample_size=entry.sample_size,
                    rank=rank,
                    verified=(entry.model, entry.task_type) in verified,
                )
            )

    return result
