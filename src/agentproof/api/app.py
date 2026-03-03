"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentproof import __version__
from agentproof.api.routes import events, health, stats
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
