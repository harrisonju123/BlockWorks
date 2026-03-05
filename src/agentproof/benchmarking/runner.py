"""Benchmark runner: evaluate models against a gold-standard reference.

Loads eval prompts, generates reference completions from a strong model (Opus),
replays each prompt to benchmark models, scores via LLM-as-judge, and writes
results to the benchmark_results table. Resumable — skips (prompt_hash, model)
pairs already completed under org_id='eval-v1'.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import asyncpg
import litellm

from agentproof.benchmarking.eval_set import EvalPrompt, load_eval_set, to_messages
from agentproof.benchmarking.judge import evaluate
from agentproof.benchmarking.mirror import _BENCH_COLUMNS, _replay_prompt
from agentproof.models import MODEL_CATALOG
from agentproof.types import TaskType
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)

EVAL_ORG_ID = "eval-v1"

# Sentinel UUID for eval prompts (no real event behind them)
_EVAL_EVENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass
class PromptResult:
    """Result of benchmarking one prompt against one model."""

    prompt_hash: str
    benchmark_model: str
    task_type: TaskType
    quality_score: float
    benchmark_cost: float
    benchmark_latency_ms: float
    reference_cost: float
    reference_latency_ms: float
    judge_model: str
    rubric_version: str


@dataclass
class RunStats:
    """Accumulated stats for the benchmark run."""

    completed: int = 0
    skipped: int = 0
    failed: int = 0
    total_cost: float = 0.0
    total_time_s: float = 0.0


@dataclass
class BenchmarkRunner:
    """Runs the eval set through benchmark models with LLM-as-judge scoring.

    Resumable: queries DB for already-completed (prompt_hash, model) pairs
    tagged with org_id='eval-v1', and skips them.
    """

    db_url: str
    reference_model: str = "claude-opus-4-6"
    judge_model: str = "claude-sonnet-4-6"
    concurrency: int = 5
    batch_size: int = 20
    benchmark_models: list[str] = field(default_factory=list)
    task_types: set[TaskType] | None = None
    api_base: str | None = None  # LiteLLM proxy URL (e.g. http://litellm:4000)

    # Runtime state
    _pool: asyncpg.Pool | None = field(default=None, repr=False)
    _semaphore: asyncio.Semaphore | None = field(default=None, repr=False)
    _reference_cache: dict[str, tuple[str, float, float]] = field(
        default_factory=dict, repr=False
    )
    _completed_pairs: set[tuple[str, str]] = field(
        default_factory=set, repr=False
    )
    _stats: RunStats = field(default_factory=RunStats, repr=False)
    _resume_loaded: bool = field(default=False, repr=False)

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.db_url, min_size=2, max_size=10)
        return self._pool

    async def load_resume_state(self) -> None:
        """Load already-completed (prompt_hash, model) pairs from DB."""
        if self._resume_loaded:
            return
        pool = await self._ensure_pool()
        # prompt_hash stored in original_model column for eval runs
        rows = await pool.fetch(
            """
            SELECT DISTINCT original_model, benchmark_model
            FROM benchmark_results
            WHERE org_id = $1
            """,
            EVAL_ORG_ID,
        )
        self._completed_pairs = {
            (row["original_model"], row["benchmark_model"]) for row in rows
        }
        self._resume_loaded = True
        if self._completed_pairs:
            logger.info(
                "Found %d already-completed (prompt, model) pairs — will skip",
                len(self._completed_pairs),
            )

    def _is_done(self, prompt_hash: str, model: str) -> bool:
        """Check if this (prompt, model) pair was already benchmarked."""
        return (prompt_hash, model) in self._completed_pairs

    async def _get_reference(
        self, prompt: EvalPrompt, messages: list[dict],
    ) -> tuple[str, float, float]:
        """Get reference completion from the strong model, with caching."""
        if prompt.prompt_hash in self._reference_cache:
            return self._reference_cache[prompt.prompt_hash]

        completion, cost, latency = await _replay_prompt(
            messages, self.reference_model, api_base=self.api_base
        )
        self._reference_cache[prompt.prompt_hash] = (completion, cost, latency)
        return completion, cost, latency

    async def _benchmark_one(
        self,
        prompt: EvalPrompt,
        model: str,
        messages: list[dict],
        prompt_text: str,
        reference_completion: str,
        reference_cost: float,
        reference_latency: float,
        total: int,
    ) -> PromptResult | None:
        """Benchmark a single (prompt, model) pair."""
        if self._semaphore is None:
            raise RuntimeError("Runner not initialized — call run() first")
        async with self._semaphore:
            start = time.monotonic()
            try:
                bench_completion, bench_cost, bench_latency = await _replay_prompt(
                    messages, model, api_base=self.api_base
                )
                quality_score, rubric_version = await evaluate(
                    original_prompt=prompt_text,
                    original_completion=reference_completion,
                    benchmark_completion=bench_completion,
                    task_type=prompt.task_type,
                    judge_model=self.judge_model,
                    api_base=self.api_base,
                )

                elapsed = time.monotonic() - start
                self._stats.completed += 1
                self._stats.total_cost += bench_cost

                logger.info(
                    "[%d/%d] %s | %s | quality=%.2f | $%.4f | %.1fs",
                    self._stats.completed, total,
                    prompt.task_type.value, model,
                    quality_score, bench_cost, elapsed,
                )

                return PromptResult(
                    prompt_hash=prompt.prompt_hash,
                    benchmark_model=model,
                    task_type=prompt.task_type,
                    quality_score=quality_score,
                    benchmark_cost=bench_cost,
                    benchmark_latency_ms=bench_latency,
                    reference_cost=reference_cost,
                    reference_latency_ms=reference_latency,
                    judge_model=self.judge_model,
                    rubric_version=rubric_version,
                )

            except litellm.AuthenticationError:
                logger.warning("Skipping %s — missing API key", model)
                self._stats.failed += 1
                return None
            except Exception:
                logger.exception(
                    "Benchmark failed: %s x %s", prompt.task_type.value, model
                )
                self._stats.failed += 1
                return None

    async def _write_batch(self, results: list[PromptResult]) -> None:
        """Write a batch of results to the benchmark_results table."""
        if not results:
            return

        pool = await self._ensure_pool()
        now = utcnow()
        rows = [
            (
                uuid.uuid4(),
                now,
                _EVAL_EVENT_ID,
                r.prompt_hash,  # store prompt_hash in original_model for resumability
                r.benchmark_model,
                r.task_type.value,
                r.quality_score,
                r.reference_cost,
                r.benchmark_cost,
                r.reference_latency_ms,
                r.benchmark_latency_ms,
                r.judge_model,
                r.rubric_version,
                EVAL_ORG_ID,
            )
            for r in results
        ]

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.copy_records_to_table(
                        "benchmark_results", records=rows, columns=_BENCH_COLUMNS
                    )
            logger.debug("Wrote %d benchmark results", len(results))
        except Exception:
            # Adjust stats so completed count reflects what's actually persisted
            self._stats.completed -= len(results)
            self._stats.failed += len(results)
            logger.exception("Failed to write batch of %d results", len(results))

    async def estimate_cost(self, prompts: list[EvalPrompt]) -> dict:
        """Estimate cost without running anything. Loads resume state if needed."""
        await self.load_resume_state()

        pairs = []
        skipped = 0
        for p in prompts:
            for m in self.benchmark_models:
                if self._is_done(p.prompt_hash, m):
                    skipped += 1
                else:
                    pairs.append((p, m))

        # Rough cost estimates based on model catalog
        ref_info = MODEL_CATALOG.get(self.reference_model)
        judge_info = MODEL_CATALOG.get(self.judge_model)

        # Average ~500 input tokens, ~800 output tokens per call
        avg_input_k = 0.5
        avg_output_k = 0.8

        ref_cost_per_call = 0.0
        if ref_info:
            ref_cost_per_call = (
                ref_info.cost_per_1k_input * avg_input_k
                + ref_info.cost_per_1k_output * avg_output_k
            )

        judge_cost_per_call = 0.0
        if judge_info:
            judge_cost_per_call = (
                judge_info.cost_per_1k_input * avg_input_k * 3  # judge prompt is larger
                + judge_info.cost_per_1k_output * 0.2  # judge output is small
            )

        bench_cost = 0.0
        for _, m in pairs:
            info = MODEL_CATALOG.get(m)
            if info:
                bench_cost += (
                    info.cost_per_1k_input * avg_input_k
                    + info.cost_per_1k_output * avg_output_k
                )

        # Reference calls: one per unique prompt
        unique_prompts = {p.prompt_hash for p, _ in pairs}
        ref_total = len(unique_prompts) * ref_cost_per_call
        judge_total = len(pairs) * judge_cost_per_call

        return {
            "total_pairs": len(pairs),
            "unique_prompts": len(unique_prompts),
            "models": self.benchmark_models,
            "reference_cost_estimate": round(ref_total, 2),
            "benchmark_cost_estimate": round(bench_cost, 2),
            "judge_cost_estimate": round(judge_total, 2),
            "total_cost_estimate": round(ref_total + bench_cost + judge_total, 2),
            "skipped_already_done": skipped,
        }

    async def run(self, prompts: list[EvalPrompt] | None = None) -> RunStats:
        """Execute the full benchmark run.

        Loads prompts, pre-warms reference completions, then fans out benchmark
        calls concurrently (gated by semaphore). Writes results in batches.
        """
        if prompts is None:
            prompts = load_eval_set(task_types=self.task_types)

        await self.load_resume_state()
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._stats = RunStats()

        # Build work items, skipping already-done pairs
        work: list[tuple[EvalPrompt, str]] = []
        for p in prompts:
            for m in self.benchmark_models:
                if self._is_done(p.prompt_hash, m):
                    self._stats.skipped += 1
                else:
                    work.append((p, m))

        if not work:
            logger.info("All (prompt, model) pairs already completed — nothing to do")
            return self._stats

        total = len(work)
        logger.info(
            "Running %d benchmark pairs (%d skipped as already done)",
            total, self._stats.skipped,
        )

        run_start = time.monotonic()

        # Pre-compute messages and prompt_text once per unique prompt
        unique_prompts = {p.prompt_hash: p for p, _ in work}
        messages_cache: dict[str, list[dict]] = {}
        prompt_text_cache: dict[str, str] = {}
        for phash, prompt in unique_prompts.items():
            msgs = to_messages(prompt)
            messages_cache[phash] = msgs
            prompt_text_cache[phash] = json.dumps(msgs)

        # Pre-warm reference completions sequentially (one Opus call per unique prompt)
        logger.info("Pre-warming %d reference completions...", len(unique_prompts))
        for phash, prompt in unique_prompts.items():
            await self._get_reference(prompt, messages_cache[phash])

        # Fan out benchmark calls with semaphore-gated concurrency
        async def _run_one(prompt: EvalPrompt, model: str) -> PromptResult | None:
            ref_completion, ref_cost, ref_latency = self._reference_cache[prompt.prompt_hash]
            return await self._benchmark_one(
                prompt, model,
                messages_cache[prompt.prompt_hash],
                prompt_text_cache[prompt.prompt_hash],
                ref_completion, ref_cost, ref_latency,
                total,
            )

        results = await asyncio.gather(
            *[_run_one(p, m) for p, m in work],
            return_exceptions=True,
        )

        # Collect successful results and write in batches
        pending: list[PromptResult] = []
        for r in results:
            if isinstance(r, PromptResult):
                pending.append(r)
            elif isinstance(r, Exception):
                logger.exception("Unexpected error in benchmark gather: %s", r)
                self._stats.failed += 1

            if len(pending) >= self.batch_size:
                await self._write_batch(pending)
                pending = []

        if pending:
            await self._write_batch(pending)

        # Clean up reference cache after run completes
        self._reference_cache.clear()

        self._stats.total_time_s = time.monotonic() - run_start

        logger.info(
            "Benchmark run complete: %d done, %d skipped, %d failed, $%.2f, %.1fs",
            self._stats.completed, self._stats.skipped, self._stats.failed,
            self._stats.total_cost, self._stats.total_time_s,
        )

        return self._stats

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
