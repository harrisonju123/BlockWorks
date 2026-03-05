#!/usr/bin/env python3
"""Post-benchmark validation: sanity-check routing recommendations.

Queries the benchmark_results table to verify that benchmark data makes sense
before trusting it for routing.

Usage:
    docker compose exec api python /app/scripts/validate_fitness.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

import asyncpg

from agentproof.config import get_config
from agentproof.benchmarking.runner import EVAL_ORG_ID
from agentproof.benchmarking.types import FitnessEntry
from agentproof.models import MODEL_CATALOG, get_tier
from agentproof.waste.suggest import _SIMPLE_TASKS as _SIMPLE_TASK_ENUMS
from agentproof.waste.suggest import suggest_alternative

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

QUALITY_THRESHOLD = 0.85
MIN_SAMPLE_SIZE = 15

_SIMPLE_TASKS = {t.value for t in _SIMPLE_TASK_ENUMS}
_COMPLEX_TASKS = {"code_generation", "reasoning", "code_review"}


class ValidationResult:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append((name, passed, detail))
        icon = "PASS" if passed else "FAIL"
        logger.info("  [%s] %s: %s", icon, name, detail)

    @property
    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def summary(self) -> str:
        passed = sum(1 for _, ok, _ in self.checks if ok)
        total = len(self.checks)
        return f"{passed}/{total} checks passed"


async def validate(pool: asyncpg.Pool) -> ValidationResult:
    result = ValidationResult()

    # Single query — all other checks derived from this
    logger.info("\n--- Loading benchmark data ---")
    rows = await pool.fetch(
        """
        SELECT benchmark_model, task_type, AVG(quality_score) as avg_q, COUNT(*) as n
        FROM benchmark_results
        WHERE org_id = $1
        GROUP BY benchmark_model, task_type
        ORDER BY benchmark_model, task_type
        """,
        EVAL_ORG_ID,
    )

    if not rows:
        result.check("has_data", False, "No eval benchmark data found")
        return result

    result.check("has_data", True, f"{len(rows)} (model, task_type) cells")

    # 1. Sample sizes
    logger.info("\n--- Sample Size Checks ---")
    for row in rows:
        n = row["n"]
        result.check(
            f"sample_size/{row['benchmark_model'][:20]}/{row['task_type']}",
            n >= MIN_SAMPLE_SIZE,
            f"n={n} (min={MIN_SAMPLE_SIZE})",
        )

    # 2. Quality ordering: tier 2 should generally beat tier 3
    logger.info("\n--- Quality Ordering Checks ---")
    quality_map: dict[tuple[str, str], float] = {}
    for row in rows:
        quality_map[(row["task_type"], row["benchmark_model"])] = float(row["avg_q"])

    task_types = {row["task_type"] for row in rows}
    for tt in sorted(task_types):
        tier2_scores = []
        tier3_scores = []
        for row in rows:
            if row["task_type"] != tt:
                continue
            tier = get_tier(row["benchmark_model"])
            if tier == 2:
                tier2_scores.append(float(row["avg_q"]))
            elif tier == 3:
                tier3_scores.append(float(row["avg_q"]))

        if tier2_scores and tier3_scores:
            avg_t2 = sum(tier2_scores) / len(tier2_scores)
            avg_t3 = sum(tier3_scores) / len(tier3_scores)
            result.check(
                f"tier_order/{tt}",
                avg_t2 >= avg_t3,
                f"tier2={avg_t2:.3f} vs tier3={avg_t3:.3f}",
            )

    # 3. Simple tasks: tier 3 should qualify at 0.85 threshold
    logger.info("\n--- Task Difficulty Checks ---")
    for tt in _SIMPLE_TASKS & task_types:
        tier3_quals = [
            quality_map[(tt, row["benchmark_model"])]
            for row in rows
            if row["task_type"] == tt and get_tier(row["benchmark_model"]) == 3
        ]
        if tier3_quals:
            best_t3 = max(tier3_quals)
            result.check(
                f"simple_task_t3/{tt}",
                best_t3 >= QUALITY_THRESHOLD,
                f"best tier3 quality={best_t3:.3f} (threshold={QUALITY_THRESHOLD})",
            )

    # 4. Complex tasks: tier 2 should qualify
    for tt in _COMPLEX_TASKS & task_types:
        tier2_quals = [
            quality_map[(tt, row["benchmark_model"])]
            for row in rows
            if row["task_type"] == tt and get_tier(row["benchmark_model"]) == 2
        ]
        if tier2_quals:
            best_t2 = max(tier2_quals)
            result.check(
                f"complex_task_t2/{tt}",
                best_t2 >= QUALITY_THRESHOLD,
                f"best tier2 quality={best_t2:.3f} (threshold={QUALITY_THRESHOLD})",
            )

    # 5. No model should have quality > 0.95 across all tasks (judge bias signal)
    logger.info("\n--- Judge Bias Checks ---")
    model_totals: dict[str, list[float]] = {}
    for row in rows:
        model_totals.setdefault(row["benchmark_model"], []).append(float(row["avg_q"]))
    for model, scores in model_totals.items():
        avg_q = sum(scores) / len(scores)
        result.check(
            f"no_judge_bias/{model[:25]}",
            avg_q <= 0.95,
            f"avg_quality={avg_q:.3f} (should be <=0.95)",
        )

    # 6. Routing recommendation spot-check
    logger.info("\n--- Routing Spot Checks ---")
    fitness_entries = []
    for row in rows:
        info = MODEL_CATALOG.get(row["benchmark_model"])
        avg_cost = info.avg_cost if info else 0.01
        fitness_entries.append(
            FitnessEntry(
                task_type=row["task_type"],
                model=row["benchmark_model"],
                avg_quality=float(row["avg_q"]),
                avg_cost=avg_cost,
                avg_latency=500.0,
                sample_size=row["n"],
            )
        )

    for tt in sorted(_SIMPLE_TASKS & task_types):
        suggestion = suggest_alternative(
            task_type=tt,
            current_model="claude-opus-4-6",
            fitness_entries=fitness_entries,
            quality_threshold=QUALITY_THRESHOLD,
        )
        if suggestion:
            detail = (
                f"suggests {suggestion.suggested_model} "
                f"(q={suggestion.quality:.3f}, source={suggestion.source})"
                if suggestion.quality is not None
                else f"suggests {suggestion.suggested_model} (heuristic)"
            )
            result.check(f"routing/{tt}/opus_downgrade", True, detail)
        else:
            result.check(
                f"routing/{tt}/opus_downgrade",
                False,
                "no suggestion returned — fitness data may be insufficient",
            )

    return result


async def main() -> int:
    config = get_config()
    pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=3)

    try:
        logger.info("=== Fitness Matrix Validation ===")
        result = await validate(pool)
        print(f"\n{'=' * 40}")
        print(f"  {result.summary}")
        print(f"{'=' * 40}")
        return 0 if result.all_passed else 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
