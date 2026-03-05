"""Tests for the discovery bridge — capability query, endpoint resolution, adapters."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.channels.manager import ChannelManager
from blockthrough.channels.types import ChannelConfig
from blockthrough.interop.adapters.crewai_adapter import CrewAIAdapter
from blockthrough.interop.adapters.generic_adapter import GenericHTTPAdapter
from blockthrough.interop.adapters.langchain_adapter import LangChainAdapter
from blockthrough.interop.bridge import DiscoveryBridge
from blockthrough.registry.store import ListingNotFoundError, RegistryStore
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
        name="Test Agent",
        description="A test agent",
        owner_address="0xowner1",
        category=ListingCategory.AGENT,
        pricing_model=PricingModel.FREE,
        stake_amount=0.05,
        endpoint_url="http://localhost:9000/invoke",
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)


def _make_mcp_listing(**overrides) -> MCPServerListing:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="Test MCP",
        description="A test MCP server",
        owner_address="0xmcp_owner",
        category=ListingCategory.MCP_SERVER,
        stake_amount=0.05,
        supported_methods=["search", "execute"],
        endpoint_url="http://localhost:9001/mcp",
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return MCPServerListing(**defaults)


def _make_bridge() -> tuple[DiscoveryBridge, RegistryStore]:
    store = RegistryStore(min_stake=0.01)
    bridge = DiscoveryBridge(registry=store)
    return bridge, store


# ---------------------------------------------------------------------------
# Discover agents
# ---------------------------------------------------------------------------


class TestDiscoverAgents:

    def test_find_by_name(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(_make_listing(name="Code Generator"))
        store.register_listing(_make_listing(name="Image Classifier"))

        caps = bridge.discover_agents("code")
        assert len(caps) == 1
        assert caps[0].supported_frameworks == ["generic"]

    def test_find_by_description(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(
            _make_listing(name="Agent", description="Python code generator")
        )

        caps = bridge.discover_agents("python")
        assert len(caps) == 1

    def test_empty_results(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(_make_listing(name="Agent"))

        caps = bridge.discover_agents("nonexistent-capability")
        assert caps == []

    def test_mcp_server_includes_methods(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(
            _make_mcp_listing(name="Search MCP", supported_methods=["search", "list"])
        )

        caps = bridge.discover_agents("search")
        assert len(caps) == 1
        assert caps[0].methods == ["search", "list"]

    def test_framework_from_tags(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(
            _make_listing(name="LangChain Agent", tags=["langchain", "ai"])
        )

        caps = bridge.discover_agents("langchain")
        assert len(caps) == 1
        assert "langchain" in caps[0].supported_frameworks

    def test_multiple_frameworks_from_tags(self) -> None:
        bridge, store = _make_bridge()
        store.register_listing(
            _make_listing(
                name="Multi Agent",
                tags=["langchain", "crewai"],
                description="Multi-framework agent",
            )
        )

        caps = bridge.discover_agents("multi")
        assert len(caps) == 1
        assert set(caps[0].supported_frameworks) == {"langchain", "crewai"}


# ---------------------------------------------------------------------------
# Resolve endpoint
# ---------------------------------------------------------------------------


class TestResolveEndpoint:

    def test_resolve_returns_endpoint(self) -> None:
        bridge, store = _make_bridge()
        created = store.register_listing(
            _make_listing(endpoint_url="http://localhost:9000/invoke")
        )

        resolution = bridge.resolve_endpoint(created.id)
        assert resolution.endpoint_url == "http://localhost:9000/invoke"
        assert resolution.framework == "generic"
        assert resolution.listing.id == created.id

    def test_resolve_with_framework_tag(self) -> None:
        bridge, store = _make_bridge()
        created = store.register_listing(
            _make_listing(
                endpoint_url="http://localhost:9000",
                tags=["langchain"],
            )
        )

        resolution = bridge.resolve_endpoint(created.id)
        assert resolution.framework == "langchain"

    def test_resolve_not_found_raises(self) -> None:
        bridge, store = _make_bridge()
        with pytest.raises(ListingNotFoundError):
            bridge.resolve_endpoint("nonexistent")

    def test_resolve_no_endpoint_raises(self) -> None:
        bridge, store = _make_bridge()
        created = store.register_listing(
            _make_listing(endpoint_url="")
        )

        with pytest.raises(ValueError, match="no endpoint_url"):
            bridge.resolve_endpoint(created.id)


# ---------------------------------------------------------------------------
# Get adapter
# ---------------------------------------------------------------------------


class TestGetAdapter:

    def test_get_langchain_adapter(self) -> None:
        bridge, _ = _make_bridge()
        adapter = bridge.get_adapter("langchain")
        assert isinstance(adapter, LangChainAdapter)

    def test_get_crewai_adapter(self) -> None:
        bridge, _ = _make_bridge()
        adapter = bridge.get_adapter("crewai")
        assert isinstance(adapter, CrewAIAdapter)

    def test_get_generic_adapter(self) -> None:
        bridge, _ = _make_bridge()
        adapter = bridge.get_adapter("generic")
        assert isinstance(adapter, GenericHTTPAdapter)

    def test_unknown_framework_raises(self) -> None:
        bridge, _ = _make_bridge()
        with pytest.raises(ValueError, match="Unknown framework"):
            bridge.get_adapter("autogen")

    def test_adapter_caching(self) -> None:
        """Same adapter instance is returned for repeated calls."""
        bridge, _ = _make_bridge()
        a1 = bridge.get_adapter("langchain")
        a2 = bridge.get_adapter("langchain")
        assert a1 is a2

    def test_reset_clears_cache(self) -> None:
        bridge, _ = _make_bridge()
        a1 = bridge.get_adapter("langchain")
        bridge.reset()
        a2 = bridge.get_adapter("langchain")
        assert a1 is not a2


# ---------------------------------------------------------------------------
# Negotiate payment
# ---------------------------------------------------------------------------


class TestNegotiatePayment:

    def test_returns_none_without_channel_manager(self) -> None:
        bridge, _ = _make_bridge()
        result = bridge.negotiate_payment("caller", "target", 1.0)
        assert result is None

    def test_opens_channel_with_manager(self) -> None:
        store = RegistryStore(min_stake=0.01)
        manager = ChannelManager(config=ChannelConfig(min_deposit=0.01))
        bridge = DiscoveryBridge(registry=store, channel_manager=manager)

        channel_id = bridge.negotiate_payment("caller", "target", 1.0)
        assert channel_id is not None
        assert len(channel_id) == 36  # UUID format

        # Verify the channel was actually created
        channel = manager.get_channel(channel_id)
        assert channel.sender == "caller"
        assert channel.receiver == "target"
        assert channel.deposit_amount == 1.0
