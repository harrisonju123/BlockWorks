"""FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentproof import __version__
from agentproof.api.routes import (
    alerts,
    attestation,
    benchmarks,
    channels,
    enterprise,
    events,
    fitness,
    governance,
    health,
    ingest,
    interop,
    mcp,
    proxy,
    registry,
    revenue,
    routing,
    stats,
    trust,
    validators,
    workflows,
)
from agentproof.benchmarking.mirror import BenchmarkWorker
from agentproof.benchmarking.types import BenchmarkConfig
from agentproof.config import get_config
from agentproof.db.queries import get_active_routing_policy, get_fitness_matrix
from agentproof.pipeline.writer import EventWriter
from agentproof.routing.policy import PolicyValidationError, default_policy, load_policy
from agentproof.routing.router import FitnessCache, generate_synthetic_fitness, merge_fitness_entries
from agentproof.routing.writer import DecisionRecord, RoutingDecisionWriter
from agentproof.types import LLMEvent

logger = logging.getLogger(__name__)


async def _refresh_fitness_cache(app: FastAPI, interval_s: int) -> None:
    """Periodically refresh the FitnessCache by merging synthetic base + real DB entries."""
    while True:
        try:
            await asyncio.sleep(interval_s)
            from agentproof.api.deps import get_async_session
            async with get_async_session() as session:
                db_entries = await get_fitness_matrix(session, org_id=None)
            synthetic = generate_synthetic_fitness()
            merged = merge_fitness_entries(synthetic, db_entries)
            cache: FitnessCache = app.state.fitness_cache
            cache.update(merged)
            # Regenerate bootstrap policy with fresh benchmark data;
            # cache key includes fitness timestamp so no manual clear needed
            policy = getattr(app.state, "routing_policy", None)
            if policy is not None and policy.version == 0:
                app.state.routing_policy = default_policy(fitness_cache=cache)
            logger.debug("FitnessCache refreshed: %d merged entries", len(merged))
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("FitnessCache refresh failed, will retry")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage proxy-owned resources: httpx client, event queue, writer, routing, benchmarking."""
    cfg = get_config()

    # HTTP client for OpenAI-compatible upstream (/v1/chat/completions)
    http_client = httpx.AsyncClient(
        base_url=cfg.upstream_url,
        timeout=httpx.Timeout(300.0, connect=10.0),
    )
    app.state.http_client = http_client

    # Separate client for Anthropic-native upstream (/v1/messages).
    _anthropic_default_headers = {}
    _api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if _api_key:
        _anthropic_default_headers["x-api-key"] = _api_key
        _anthropic_default_headers["Authorization"] = f"Bearer {_api_key}"
    anthropic_client = httpx.AsyncClient(
        base_url=cfg.anthropic_upstream_url,
        timeout=httpx.Timeout(300.0, connect=10.0),
        headers=_anthropic_default_headers,
    )
    app.state.anthropic_client = anthropic_client

    # Event pipeline
    event_queue: asyncio.Queue[LLMEvent] = asyncio.Queue(
        maxsize=cfg.pipeline_queue_max_size,
    )
    app.state.event_queue = event_queue

    writer = EventWriter(
        db_url=cfg.database_url,
        queue=event_queue,
        batch_size=cfg.pipeline_batch_size,
        flush_interval_s=cfg.pipeline_flush_interval_ms / 1000.0,
    )
    writer_task = asyncio.create_task(writer.run())

    # -- Routing ---------------------------------------------------------------
    app.state.routing_enabled = cfg.routing_enabled
    decision_writer_task = None
    if cfg.routing_enabled:
        app.state.fitness_cache = FitnessCache()
        resolved_policy = None

        # Eager DB load: policy + fitness cache, merged with synthetic base
        try:
            from agentproof.api.deps import get_async_session
            from agentproof.api.routes.routing import _load_policy_from_row
            async with get_async_session() as session:
                db_policy = await get_active_routing_policy(session)
                db_entries = await get_fitness_matrix(session, org_id=None)
            if db_policy is not None:
                resolved_policy = _load_policy_from_row(db_policy)
                logger.info("Loaded routing policy v%d from DB", db_policy["version"])
            synthetic = generate_synthetic_fitness()
            merged = merge_fitness_entries(synthetic, db_entries)
            app.state.fitness_cache.update(merged)
            logger.info(
                "FitnessCache seeded: %d synthetic + %d real -> %d merged",
                len(synthetic), len(db_entries), len(merged),
            )
        except Exception:
            logger.warning("DB startup load failed — seeding with synthetic only")
            app.state.fitness_cache.update(generate_synthetic_fitness())

        # Policy priority: DB → YAML file → bootstrap
        if resolved_policy is None and cfg.routing_policy_path:
            try:
                resolved_policy = load_policy(Path(cfg.routing_policy_path))
                logger.info("Loaded routing policy from %s", cfg.routing_policy_path)
            except PolicyValidationError as e:
                logger.warning("Policy file %s failed validation: %s", cfg.routing_policy_path, e.errors)
            except Exception:
                logger.exception("Failed to load policy from %s", cfg.routing_policy_path)
        if resolved_policy is None:
            resolved_policy = default_policy(fitness_cache=app.state.fitness_cache)
            logger.info("Using bootstrap routing policy (%d rules)", len(resolved_policy.rules))
        app.state.routing_policy = resolved_policy

        fitness_refresh_task = asyncio.create_task(
            _refresh_fitness_cache(app, cfg.routing_fitness_cache_ttl_s)
        )

        # Decision writer — persists routing decisions to the hypertable
        decision_queue: asyncio.Queue[DecisionRecord] = asyncio.Queue(
            maxsize=cfg.pipeline_queue_max_size,
        )
        app.state.decision_queue = decision_queue
        decision_writer = RoutingDecisionWriter(
            db_url=cfg.database_url,
            queue=decision_queue,
        )
        decision_writer_task = asyncio.create_task(decision_writer.run())
        logger.info("Routing enabled (cache TTL %ds)", cfg.routing_fitness_cache_ttl_s)
    else:
        fitness_refresh_task = None

    # -- Attestation scheduler --------------------------------------------------
    attestation_scheduler_task = None
    if cfg.attestation_scheduler_enabled:
        from agentproof.api.routes.attestation import _get_provider
        from agentproof.attestation.scheduler import run_attestation_scheduler

        attestation_scheduler_task = asyncio.create_task(
            run_attestation_scheduler(
                _get_provider(), cfg.attestation_scheduler_interval_s
            )
        )
        logger.info(
            "Attestation scheduler enabled (interval=%ds)",
            cfg.attestation_scheduler_interval_s,
        )

    # -- Benchmarking ----------------------------------------------------------
    benchmark_worker_task = None
    if cfg.benchmark_enabled:
        bench_config = BenchmarkConfig(
            sample_rate=cfg.benchmark_sample_rate,
            benchmark_models=cfg.benchmark_models,
            judge_model=cfg.benchmark_judge_model,
        )
        app.state.benchmark_config = bench_config
        bench_queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.pipeline_queue_max_size)
        app.state.benchmark_queue = bench_queue
        benchmark_worker = BenchmarkWorker(
            db_url=cfg.database_url,
            queue=bench_queue,
            config=bench_config,
        )
        benchmark_worker_task = asyncio.create_task(benchmark_worker.run())
        logger.info("Benchmarking enabled (sample_rate=%.2f)", cfg.benchmark_sample_rate)

    yield

    # Shutdown — stop workers BEFORE closing httpx clients, since the
    # benchmark worker replays prompts against upstream during drain.
    if attestation_scheduler_task:
        attestation_scheduler_task.cancel()
        try:
            await attestation_scheduler_task
        except asyncio.CancelledError:
            pass

    if fitness_refresh_task:
        fitness_refresh_task.cancel()
        try:
            await fitness_refresh_task
        except asyncio.CancelledError:
            pass

    # Shut down the routing decision writer before benchmark/event workers
    if decision_writer_task:
        await decision_writer.shutdown()
        try:
            await asyncio.wait_for(decision_writer_task, timeout=5.0)
        except asyncio.TimeoutError:
            decision_writer_task.cancel()
            try:
                await decision_writer_task
            except asyncio.CancelledError:
                pass

    if benchmark_worker_task:
        await benchmark_worker.shutdown()
        try:
            await asyncio.wait_for(benchmark_worker_task, timeout=10.0)
        except asyncio.TimeoutError:
            benchmark_worker_task.cancel()
            try:
                await benchmark_worker_task
            except asyncio.CancelledError:
                pass

    await writer.shutdown()
    try:
        await asyncio.wait_for(writer_task, timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("Proxy EventWriter did not finish in 10s, cancelling")
        writer_task.cancel()
        try:
            await writer_task
        except asyncio.CancelledError:
            pass

    # Close httpx clients last — workers may need them during drain
    await http_client.aclose()
    await anthropic_client.aclose()


app = FastAPI(
    title="AgentProof",
    version=__version__,
    description="AI agent observability and benchmarking API",
    lifespan=lifespan,
)

config = get_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Proxy routes — no prefix, routes are /v1/...
app.include_router(proxy.router, tags=["proxy"])

# Dashboard API routes — all under /api/v1
app.include_router(health.router, tags=["health"])
app.include_router(stats.router, prefix="/api/v1", tags=["stats"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(alerts.router, prefix="/api/v1", tags=["alerts"])
app.include_router(benchmarks.router, prefix="/api/v1", tags=["benchmarks"])
app.include_router(routing.router, prefix="/api/v1", tags=["routing"])
app.include_router(attestation.router, prefix="/api/v1", tags=["attestations"])
app.include_router(channels.router, prefix="/api/v1", tags=["channels"])
app.include_router(governance.router, prefix="/api/v1", tags=["governance"])
app.include_router(trust.router, prefix="/api/v1", tags=["trust"])
app.include_router(validators.router, prefix="/api/v1", tags=["validators"])
app.include_router(fitness.router, prefix="/api/v1", tags=["fitness"])
app.include_router(ingest.router, prefix="/api/v1", tags=["ingest"])
app.include_router(registry.router, prefix="/api/v1", tags=["registry"])
app.include_router(enterprise.router, prefix="/api/v1", tags=["enterprise"])
app.include_router(workflows.router, prefix="/api/v1", tags=["workflows"])
app.include_router(revenue.router, prefix="/api/v1", tags=["revenue"])
app.include_router(interop.router, prefix="/api/v1", tags=["interop"])
app.include_router(mcp.router, prefix="/api/v1", tags=["mcp"])
