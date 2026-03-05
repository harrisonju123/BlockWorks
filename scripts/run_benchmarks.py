#!/usr/bin/env python3
"""CLI for running the benchmark eval set against real models.

Usage:
    docker compose exec api python /app/scripts/run_benchmarks.py
    docker compose exec api python /app/scripts/run_benchmarks.py --models claude-sonnet-4-6 claude-haiku-4-5-20251001
    docker compose exec api python /app/scripts/run_benchmarks.py --task-types code_generation classification
    docker compose exec api python /app/scripts/run_benchmarks.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from agentproof.benchmarking.eval_set import load_eval_set
from agentproof.benchmarking.runner import BenchmarkRunner
from agentproof.config import get_config
from agentproof.types import TaskType

# Default benchmark models — practical first run with common API keys
DEFAULT_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "gpt-4o",
    "gpt-4o-mini",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run benchmark evaluations against real models",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Models to benchmark (default: %(default)s)",
    )
    parser.add_argument(
        "--task-types",
        nargs="+",
        default=None,
        help="Filter to specific task types (e.g., code_generation classification)",
    )
    parser.add_argument(
        "--reference-model",
        default="claude-opus-4-6",
        help="Reference model for gold-standard completions (default: %(default)s)",
    )
    parser.add_argument(
        "--judge-model",
        default="claude-sonnet-4-6",
        help="Judge model for scoring (default: %(default)s)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent API calls (default: %(default)s)",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="LiteLLM proxy URL (e.g. http://litellm:4000). If set, all LLM calls route through the proxy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cost estimate without running",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    config = get_config()

    task_types = None
    if args.task_types:
        try:
            task_types = {TaskType(t) for t in args.task_types}
        except ValueError as e:
            logger.error("Invalid task type: %s", e)
            logger.info("Valid types: %s", [t.value for t in TaskType if t != TaskType.UNKNOWN])
            return 1

    prompts = load_eval_set(task_types=task_types)
    if not prompts:
        logger.error("No eval prompts found")
        return 1

    # Show distribution
    dist: dict[str, int] = {}
    for p in prompts:
        dist[p.task_type.value] = dist.get(p.task_type.value, 0) + 1
    logger.info("Eval set distribution:")
    for t, c in sorted(dist.items()):
        logger.info("  %-20s %d prompts", t, c)

    # Default api_base to upstream_url from config if not explicitly provided
    api_base = args.api_base or config.upstream_url

    runner = BenchmarkRunner(
        db_url=config.database_url,
        reference_model=args.reference_model,
        judge_model=args.judge_model,
        concurrency=args.concurrency,
        benchmark_models=args.models,
        task_types=task_types,
        api_base=api_base,
    )

    try:
        if args.dry_run:
            estimate = await runner.estimate_cost(prompts)
            print("\n=== Benchmark Cost Estimate ===")
            print(f"  Prompts:           {estimate['unique_prompts']}")
            print(f"  Models:            {', '.join(estimate['models'])}")
            print(f"  Total pairs:       {estimate['total_pairs']}")
            print(f"  Already done:      {estimate['skipped_already_done']}")
            print(f"  Reference cost:    ${estimate['reference_cost_estimate']:.2f}")
            print(f"  Benchmark cost:    ${estimate['benchmark_cost_estimate']:.2f}")
            print(f"  Judge cost:        ${estimate['judge_cost_estimate']:.2f}")
            print(f"  ─────────────────────────────")
            print(f"  TOTAL ESTIMATE:    ${estimate['total_cost_estimate']:.2f}")
            return 0

        stats = await runner.run(prompts)
        print(f"\n=== Benchmark Complete ===")
        print(f"  Completed: {stats.completed}")
        print(f"  Skipped:   {stats.skipped}")
        print(f"  Failed:    {stats.failed}")
        print(f"  Cost:      ${stats.total_cost:.2f}")
        print(f"  Time:      {stats.total_time_s:.1f}s")
        return 0

    finally:
        await runner.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
