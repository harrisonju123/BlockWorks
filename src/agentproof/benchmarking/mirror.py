"""Traffic mirroring: sample production events and benchmark against alternative models.

The mirror runs as a background async worker (similar to EventWriter). It consumes
LLMEvents from a queue, replays the same prompt to each benchmark model, runs the
LLM-as-judge, and writes BenchmarkResults to TimescaleDB. The entire flow is
non-blocking -- the main pipeline never waits on benchmark work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
import asyncpg
import litellm

from agentproof.benchmarking.convert import convert_anthropic_to_openai, is_anthropic_format
from agentproof.benchmarking.judge import evaluate
from agentproof.benchmarking.types import BenchmarkConfig, BenchmarkResult
from agentproof.utils import utcnow
from agentproof.pipeline.base_worker import AsyncQueueWorker
from agentproof.types import LLMEvent, TaskType

logger = logging.getLogger(__name__)

_BENCH_COLUMNS = [
    "id",
    "created_at",
    "original_event_id",
    "original_model",
    "benchmark_model",
    "task_type",
    "quality_score",
    "original_cost",
    "benchmark_cost",
    "original_latency_ms",
    "benchmark_latency_ms",
    "judge_model",
    "rubric_version",
    "org_id",
]


def should_sample(
    event: LLMEvent,
    config: BenchmarkConfig,
    messages: list[dict] | None = None,
) -> bool:
    """Decide whether to benchmark an event based on the config.

    An event is sampled when:
    1. sample_rate > 0 and the random draw passes
    2. The event's task_type is in the enabled list
    3. The event was successful (no point benchmarking failures)
    4. The event has a task_type (UNKNOWN is excluded)
    """
    if config.sample_rate <= 0.0:
        return False

    if event.status.value != "success":
        return False

    if event.task_type is None or event.task_type == TaskType.UNKNOWN:
        return False

    if event.task_type not in config.enabled_task_types:
        return False

    # sample_rate of 1.0 means always benchmark
    if config.sample_rate >= 1.0:
        return True

    return random.random() < config.sample_rate


async def _replay_prompt(
    messages: list[dict],
    model: str,
    system_prompt: str | list | None = None,
) -> tuple[str, float, float]:
    """Send the original messages to a benchmark model.

    Converts Anthropic-format messages to OpenAI format before calling litellm.
    Returns (completion_text, cost_usd, latency_ms).
    """
    replay_messages = messages
    if is_anthropic_format(messages):
        replay_messages = convert_anthropic_to_openai(messages, system=system_prompt)
    elif system_prompt:
        # OpenAI-format but system_prompt passed separately (shouldn't happen,
        # but handle gracefully by prepending)
        sys_text = system_prompt if isinstance(system_prompt, str) else " ".join(
            b.get("text", "") for b in system_prompt
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if sys_text:
            replay_messages = [{"role": "system", "content": sys_text}] + messages

    start = time.monotonic()
    response = await litellm.acompletion(
        model=model,
        messages=replay_messages,
        temperature=0.0,
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    completion = response.choices[0].message.content or ""
    cost = getattr(response, "_hidden_params", {}).get("response_cost", 0.0) or 0.0

    return completion, cost, elapsed_ms


async def run_benchmark_for_event(
    event: LLMEvent,
    messages: list[dict],
    original_completion: str,
    config: BenchmarkConfig,
    system_prompt: str | list | None = None,
) -> list[BenchmarkResult]:
    """Benchmark a single event against all configured models.

    Replays the prompt to each benchmark model, scores via LLM-as-judge,
    and returns the results. Does not write to DB -- the caller handles persistence.
    """
    results: list[BenchmarkResult] = []
    task_type_enum = event.task_type
    if task_type_enum is None:
        return results

    prompt_text = json.dumps(messages)

    async def _benchmark_single_model(model: str) -> BenchmarkResult | None:
        try:
            bench_completion, bench_cost, bench_latency = await _replay_prompt(
                messages, model, system_prompt=system_prompt
            )
            quality_score, rubric_version = await evaluate(
                original_prompt=prompt_text,
                original_completion=original_completion,
                benchmark_completion=bench_completion,
                task_type=task_type_enum,
                judge_model=config.judge_model,
            )
            return BenchmarkResult(
                id=uuid.uuid4(),
                created_at=utcnow(),
                original_event_id=event.id,
                original_model=event.model,
                benchmark_model=model,
                task_type=task_type_enum,
                quality_score=quality_score,
                original_cost=event.estimated_cost,
                benchmark_cost=bench_cost,
                original_latency_ms=event.latency_ms,
                benchmark_latency_ms=bench_latency,
                judge_model=config.judge_model,
                rubric_version=rubric_version,
                org_id=event.org_id,
            )
        except Exception:
            logger.exception(
                "Benchmark failed for event=%s model=%s", event.id, model
            )
            return None

    models_to_bench = [m for m in config.benchmark_models if m != event.model]
    completed = await asyncio.gather(
        *[_benchmark_single_model(m) for m in models_to_bench],
        return_exceptions=False,
    )
    return [r for r in completed if r is not None]


# Queue item type: (event, messages, original_completion, system_prompt)
_BenchmarkItem = tuple[LLMEvent, list[dict], str, str | list | None]


class BenchmarkWorker(AsyncQueueWorker[_BenchmarkItem]):
    """Background worker that consumes events and produces benchmark results.

    Inherits pool management and shutdown from AsyncQueueWorker. Overrides
    run() because it processes items one at a time via external API calls
    rather than batching for COPY inserts.
    """

    def __init__(
        self,
        db_url: str,
        queue: asyncio.Queue[_BenchmarkItem],
        config: BenchmarkConfig,
    ) -> None:
        super().__init__(
            db_url=db_url,
            queue=queue,
            batch_size=1,
            flush_interval_s=1.0,
            pool_min=1,
            pool_max=5,
        )
        self._config = config

    def _make_item_id(self, item: _BenchmarkItem) -> str:
        return str(item[0].id)

    async def _flush(self, pool: asyncpg.Pool, batch: list[_BenchmarkItem]) -> None:
        """Not used by BenchmarkWorker -- processing happens in run()."""
        raise NotImplementedError("BenchmarkWorker uses _write_results, not _flush")

    def update_config(self, config: BenchmarkConfig) -> None:
        """Hot-reload the benchmark configuration."""
        self._config = config

    async def run(self) -> None:
        """Main loop: pull events, benchmark them, write results.

        Overrides the base batch-drain loop because each item requires
        external API calls (replay + judge) rather than a simple DB write.
        """
        pool = await self._ensure_pool()

        try:
            while not self._shutdown_event.is_set():
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                event, messages, original_completion, system_prompt = item
                try:
                    results = await run_benchmark_for_event(
                        event, messages, original_completion, self._config,
                        system_prompt=system_prompt,
                    )
                    if results:
                        await self._write_results(pool, results)
                except Exception:
                    logger.exception("BenchmarkWorker error processing event=%s", event.id)

        except asyncio.CancelledError:
            logger.info("BenchmarkWorker cancelled, draining queue")

        # Drain remaining items
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                event, messages, original_completion, system_prompt = item
                results = await run_benchmark_for_event(
                    event, messages, original_completion, self._config,
                    system_prompt=system_prompt,
                )
                if results:
                    await self._write_results(pool, results)
            except asyncio.QueueEmpty:
                break
            except Exception:
                logger.exception("BenchmarkWorker error during drain")

        await self._close_pool()
        logger.info("BenchmarkWorker shut down cleanly")

    async def _write_results(
        self, pool: asyncpg.Pool, results: list[BenchmarkResult]
    ) -> None:
        """Batch insert benchmark results into TimescaleDB."""
        rows = [
            (
                r.id,
                r.created_at,
                r.original_event_id,
                r.original_model,
                r.benchmark_model,
                r.task_type.value,
                r.quality_score,
                r.original_cost,
                r.benchmark_cost,
                r.original_latency_ms,
                r.benchmark_latency_ms,
                r.judge_model,
                r.rubric_version,
                r.org_id,
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
            logger.exception("Failed to write benchmark results")
