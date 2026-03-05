"""Tests for discovery layer — find best agent, compatible MCP servers, recommendations."""

from __future__ import annotations

from datetime import datetime, timezone

from blockthrough.registry.discovery import (
    find_best_agent,
    find_compatible_mcp_servers,
    get_recommendations,
)
from blockthrough.registry.store import RegistryStore
from blockthrough.registry.types import (
    AgentListing,
    ListingCategory,
    MCPServerListing,
    PricingModel,
)


def _make_listing(**overrides) -> AgentListing:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="Agent",
        description="Generic agent",
        owner_address="0xowner",
        category=ListingCategory.AGENT,
        stake_amount=0.05,
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)


def _make_mcp(**overrides) -> MCPServerListing:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="MCP Server",
        description="Generic MCP",
        owner_address="0xmcp",
        category=ListingCategory.MCP_SERVER,
        stake_amount=0.05,
        supported_methods=["search"],
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return MCPServerListing(**defaults)


class TestFindBestAgent:

    def test_returns_best_by_trust_and_quality(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="Good",
                trust_score=0.9,
                benchmark_performance={"code_generation": 0.85},
            )
        )
        store.register_listing(
            _make_listing(
                name="Mediocre",
                trust_score=0.5,
                benchmark_performance={"code_generation": 0.5},
            )
        )

        best = find_best_agent(store, "code_generation")
        assert best is not None
        assert best.name == "Good"

    def test_returns_none_when_no_agents(self) -> None:
        store = RegistryStore()
        assert find_best_agent(store, "code_generation") is None

    def test_price_filter(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="Affordable",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=0.001,
                trust_score=0.8,
                benchmark_performance={"code_generation": 0.7},
            )
        )
        store.register_listing(
            _make_listing(
                name="Expensive",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=10.0,
                trust_score=0.9,
                benchmark_performance={"code_generation": 0.9},
            )
        )

        best = find_best_agent(store, "code_generation", max_price=0.01)
        assert best is not None
        assert best.name == "Affordable"

    def test_quality_filter(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="High Quality",
                trust_score=0.7,
                benchmark_performance={"code_generation": 0.9},
            )
        )
        store.register_listing(
            _make_listing(
                name="Low Quality",
                trust_score=0.9,
                benchmark_performance={"code_generation": 0.3},
            )
        )

        best = find_best_agent(store, "code_generation", min_quality=0.5)
        assert best is not None
        assert best.name == "High Quality"

    def test_skips_mcp_servers(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_mcp(
                name="MCP",
                trust_score=1.0,
            )
        )
        store.register_listing(
            _make_listing(
                name="Agent",
                trust_score=0.5,
                benchmark_performance={"code_generation": 0.5},
            )
        )

        best = find_best_agent(store, "code_generation")
        assert best is not None
        assert best.name == "Agent"

    def test_skips_inactive_listings(self) -> None:
        store = RegistryStore()
        sus = store.register_listing(
            _make_listing(
                name="Suspended",
                trust_score=1.0,
                benchmark_performance={"code_generation": 1.0},
            )
        )
        store.suspend_listing(sus.id, "violation")

        store.register_listing(
            _make_listing(
                name="Active",
                trust_score=0.5,
                benchmark_performance={"code_generation": 0.5},
            )
        )

        best = find_best_agent(store, "code_generation")
        assert best is not None
        assert best.name == "Active"

    def test_free_listings_always_affordable(self) -> None:
        """Free listings should pass any max_price filter."""
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="FreeAgent",
                pricing_model=PricingModel.FREE,
                trust_score=0.8,
                benchmark_performance={"code_generation": 0.7},
            )
        )

        best = find_best_agent(store, "code_generation", max_price=0.0001)
        assert best is not None
        assert best.name == "FreeAgent"


class TestFindCompatibleMCPServers:

    def test_returns_active_mcp_servers(self) -> None:
        store = RegistryStore()
        agent = store.register_listing(_make_listing(name="Agent"))
        store.register_listing(_make_mcp(name="MCP-1"))
        store.register_listing(_make_mcp(name="MCP-2"))

        servers = find_compatible_mcp_servers(store, agent.id)
        assert len(servers) == 2

    def test_excludes_agents(self) -> None:
        store = RegistryStore()
        agent = store.register_listing(_make_listing(name="Agent"))
        store.register_listing(_make_listing(name="Other Agent"))
        store.register_listing(_make_mcp(name="MCP-1"))

        servers = find_compatible_mcp_servers(store, agent.id)
        assert len(servers) == 1
        assert servers[0].name == "MCP-1"

    def test_excludes_inactive_mcp(self) -> None:
        store = RegistryStore()
        agent = store.register_listing(_make_listing(name="Agent"))
        active = store.register_listing(_make_mcp(name="Active MCP"))
        sus = store.register_listing(_make_mcp(name="Suspended MCP"))
        store.suspend_listing(sus.id, "reason")

        servers = find_compatible_mcp_servers(store, agent.id)
        assert len(servers) == 1
        assert servers[0].name == "Active MCP"

    def test_returns_empty_for_unknown_agent(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_mcp(name="MCP"))
        servers = find_compatible_mcp_servers(store, "nonexistent")
        assert servers == []


class TestGetRecommendations:

    def test_recommends_by_tag_overlap(self) -> None:
        store = RegistryStore()
        used = store.register_listing(
            _make_listing(name="Used", tags=["ai", "code"])
        )
        good_match = store.register_listing(
            _make_listing(name="Good Match", tags=["ai", "code", "python"])
        )
        no_match = store.register_listing(
            _make_listing(name="No Match", tags=["image", "video"])
        )

        recs = get_recommendations(store, [used.id])
        names = [r.name for r in recs]
        assert "Good Match" in names
        # No Match should be ranked lower or absent
        if "No Match" in names:
            assert names.index("Good Match") < names.index("No Match")

    def test_excludes_already_used(self) -> None:
        store = RegistryStore()
        used = store.register_listing(
            _make_listing(name="Used", tags=["ai"])
        )
        new = store.register_listing(
            _make_listing(name="New", tags=["ai"])
        )

        recs = get_recommendations(store, [used.id])
        ids = {r.id for r in recs}
        assert used.id not in ids

    def test_falls_back_to_popular(self) -> None:
        """When no tag overlap, fall back to most popular listings."""
        store = RegistryStore()
        used = store.register_listing(
            _make_listing(name="Used", tags=["niche"])
        )
        popular = store.register_listing(
            _make_listing(name="Popular", total_calls=1000, tags=["other"])
        )

        recs = get_recommendations(store, [used.id])
        # Should include the popular one as a fallback
        names = [r.name for r in recs]
        assert "Popular" in names

    def test_respects_limit(self) -> None:
        store = RegistryStore()
        used = store.register_listing(
            _make_listing(name="Used", tags=["ai"])
        )
        for i in range(10):
            store.register_listing(
                _make_listing(name=f"Agent-{i}", tags=["ai"])
            )

        recs = get_recommendations(store, [used.id], limit=3)
        assert len(recs) <= 3

    def test_empty_history_returns_popular(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(name="Popular", total_calls=100)
        )

        recs = get_recommendations(store, [])
        assert len(recs) >= 1
