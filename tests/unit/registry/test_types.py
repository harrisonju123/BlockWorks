"""Tests for registry type models — validation, defaults, and serialization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from blockthrough.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    PricingModel,
    RegistrySearchQuery,
    RegistrySearchResult,
)


class TestAgentListing:

    def test_minimal_construction(self) -> None:
        now = datetime.now(timezone.utc)
        listing = AgentListing(
            id="test-1",
            name="My Agent",
            description="Does things",
            owner_address="0xabc",
            category=ListingCategory.AGENT,
            registered_at=now,
            last_active=now,
        )
        assert listing.id == "test-1"
        assert listing.pricing_model == PricingModel.FREE
        assert listing.price_per_call == 0.0
        assert listing.total_calls == 0
        assert listing.is_verified is False
        assert listing.status == ListingStatus.PENDING

    def test_trust_score_bounds(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AgentListing(
                id="x",
                name="x",
                description="x",
                owner_address="x",
                category=ListingCategory.AGENT,
                trust_score=1.5,
                registered_at=now,
                last_active=now,
            )

    def test_uptime_bounds(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AgentListing(
                id="x",
                name="x",
                description="x",
                owner_address="x",
                category=ListingCategory.AGENT,
                uptime_pct=101.0,
                registered_at=now,
                last_active=now,
            )


class TestMCPServerListing:

    def test_extends_agent_listing(self) -> None:
        now = datetime.now(timezone.utc)
        listing = MCPServerListing(
            id="mcp-1",
            name="MCP Server",
            description="Serves tools",
            owner_address="0xdef",
            category=ListingCategory.MCP_SERVER,
            supported_methods=["search", "execute"],
            avg_latency_ms=45.2,
            registered_at=now,
            last_active=now,
        )
        assert listing.supported_methods == ["search", "execute"]
        assert listing.avg_latency_ms == 45.2
        assert listing.failure_rate == 0.0
        assert listing.response_token_avg == 0.0

    def test_is_instance_of_agent_listing(self) -> None:
        now = datetime.now(timezone.utc)
        listing = MCPServerListing(
            id="mcp-1",
            name="MCP Server",
            description="Serves tools",
            owner_address="0xdef",
            category=ListingCategory.MCP_SERVER,
            registered_at=now,
            last_active=now,
        )
        assert isinstance(listing, AgentListing)


class TestRegistrySearchQuery:

    def test_defaults(self) -> None:
        q = RegistrySearchQuery()
        assert q.query is None
        assert q.limit == 20
        assert q.offset == 0
        assert q.sort_by == "trust"

    def test_limit_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RegistrySearchQuery(limit=0)

        with pytest.raises(ValidationError):
            RegistrySearchQuery(limit=101)


class TestRegistrySearchResult:

    def test_empty_result(self) -> None:
        result = RegistrySearchResult(
            listings=[], total_count=0, has_more=False
        )
        assert result.listings == []
        assert result.has_more is False


class TestEnums:

    def test_listing_status_values(self) -> None:
        assert ListingStatus.PENDING.value == "pending"
        assert ListingStatus.ACTIVE.value == "active"
        assert ListingStatus.SUSPENDED.value == "suspended"
        assert ListingStatus.DEPRECATED.value == "deprecated"

    def test_pricing_model_values(self) -> None:
        assert PricingModel.PER_CALL.value == "per_call"
        assert PricingModel.SUBSCRIPTION.value == "subscription"
        assert PricingModel.FREE.value == "free"

    def test_listing_category_values(self) -> None:
        assert ListingCategory.AGENT.value == "agent"
        assert ListingCategory.MCP_SERVER.value == "mcp_server"
