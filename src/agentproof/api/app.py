"""FastAPI application entry point."""

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
    registry,
    revenue,
    routing,
    stats,
    trust,
    validators,
    workflows,
)
from agentproof.config import get_config

app = FastAPI(
    title="AgentProof",
    version=__version__,
    description="AI agent observability and benchmarking API",
)

config = get_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
