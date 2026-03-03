"""Pydantic models for the agent & MCP server registry.

Defines listing types, search queries, and enums for the curated
marketplace where developers publish agents and MCP servers with
independently verified metrics.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class ListingStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPRECATED = "deprecated"


class PricingModel(str, enum.Enum):
    PER_CALL = "per_call"
    SUBSCRIPTION = "subscription"
    FREE = "free"


class ListingCategory(str, enum.Enum):
    AGENT = "agent"
    MCP_SERVER = "mcp_server"


class AgentListing(BaseModel):
    """A registered agent or MCP server in the marketplace."""

    id: str
    name: str
    description: str
    owner_address: str
    category: ListingCategory
    pricing_model: PricingModel = PricingModel.FREE
    price_per_call: float = 0.0
    trust_score: float = Field(default=0.0, ge=0.0, le=1.0)
    benchmark_performance: dict[str, float] = Field(default_factory=dict)
    uptime_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    total_calls: int = 0
    registered_at: datetime
    last_active: datetime
    stake_amount: float = 0.0
    is_verified: bool = False
    tags: list[str] = Field(default_factory=list)
    endpoint_url: str = ""
    status: ListingStatus = ListingStatus.PENDING


class MCPServerListing(AgentListing):
    """Extended listing for MCP servers with protocol-specific metrics."""

    supported_methods: list[str] = Field(default_factory=list)
    avg_latency_ms: float = 0.0
    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    response_token_avg: float = 0.0


class RegistrySearchQuery(BaseModel):
    """Parameters for searching the registry."""

    query: str | None = None
    category: ListingCategory | None = None
    min_trust_score: float | None = None
    max_price: float | None = None
    tags: list[str] = Field(default_factory=list)
    sort_by: str = "trust"  # trust, price, usage, newest
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RegistrySearchResult(BaseModel):
    """Paginated search results."""

    listings: list[AgentListing]
    total_count: int
    has_more: bool
