"""Tests for RegistryStore — register, update, search, suspend, deprecate."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.registry.store import (
    InsufficientStakeError,
    ListingNotFoundError,
    ListingPermissionError,
    RegistryStore,
)
from blockthrough.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    PricingModel,
    RegistrySearchQuery,
)
from blockthrough.trust.registry import TrustRegistry
from blockthrough.trust.types import TrustDimension


def _make_listing(**overrides) -> AgentListing:
    """Helper to build a minimal AgentListing with overrides."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="Test Agent",
        description="A test agent",
        owner_address="0xowner1",
        category=ListingCategory.AGENT,
        pricing_model=PricingModel.FREE,
        stake_amount=0.05,
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)


def _make_mcp_listing(**overrides) -> MCPServerListing:
    """Helper to build a minimal MCPServerListing with overrides."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="Test MCP",
        description="A test MCP server",
        owner_address="0xmcp_owner",
        category=ListingCategory.MCP_SERVER,
        stake_amount=0.05,
        supported_methods=["search"],
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return MCPServerListing(**defaults)


class TestRegisterListing:

    def test_assigns_uuid_id(self) -> None:
        store = RegistryStore()
        listing = _make_listing()
        created = store.register_listing(listing)
        assert created.id != ""
        assert len(created.id) == 36  # UUID format

    def test_sets_active_status(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        assert created.status == ListingStatus.ACTIVE

    def test_sets_timestamps(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        assert created.registered_at is not None
        assert created.last_active is not None

    def test_insufficient_stake_raises(self) -> None:
        store = RegistryStore(min_stake=1.0)
        listing = _make_listing(stake_amount=0.5)
        with pytest.raises(InsufficientStakeError):
            store.register_listing(listing)

    def test_mcp_listing_preserved(self) -> None:
        store = RegistryStore()
        mcp = _make_mcp_listing(supported_methods=["search", "execute"])
        created = store.register_listing(mcp)
        assert isinstance(created, MCPServerListing)
        assert created.supported_methods == ["search", "execute"]

    def test_listing_count_increments(self) -> None:
        store = RegistryStore()
        assert store.listing_count == 0
        store.register_listing(_make_listing())
        assert store.listing_count == 1

    def test_trust_score_pulled_from_registry(self) -> None:
        """When a TrustRegistry is wired, live trust scores populate the listing."""
        trust = TrustRegistry()
        trust.register_agent("0xowner1")
        trust.update_score("0xowner1", TrustDimension.RELIABILITY, 0.9)

        store = RegistryStore(trust_registry=trust)
        created = store.register_listing(_make_listing(owner_address="0xowner1"))
        # Composite should be > 0.5 because reliability is high
        assert created.trust_score > 0.5


class TestUpdateListing:

    def test_update_name(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        updated = store.update_listing(
            created.id, "0xowner1", {"name": "New Name"}
        )
        assert updated.name == "New Name"

    def test_update_preserves_other_fields(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(description="Original", tags=["ai"])
        )
        updated = store.update_listing(
            created.id, "0xowner1", {"name": "Changed"}
        )
        assert updated.description == "Original"
        assert updated.tags == ["ai"]

    def test_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            store.update_listing("nonexistent", "0xowner1", {"name": "x"})

    def test_wrong_owner_raises(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        with pytest.raises(ListingPermissionError):
            store.update_listing(created.id, "0xhacker", {"name": "Hacked"})

    def test_cannot_change_id(self) -> None:
        """ID is not in the update allowlist, so it should be ignored."""
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        updated = store.update_listing(
            created.id, "0xowner1", {"id": "sneaky-new-id"}
        )
        assert updated.id == created.id

    def test_cannot_change_status(self) -> None:
        """Status is not in the update allowlist, so it should be ignored."""
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        updated = store.update_listing(
            created.id, "0xowner1", {"status": "deprecated"}
        )
        assert updated.status == ListingStatus.ACTIVE

    def test_mcp_fields_updated(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_mcp_listing())
        updated = store.update_listing(
            created.id,
            "0xmcp_owner",
            {"supported_methods": ["search", "execute", "list"]},
        )
        assert isinstance(updated, MCPServerListing)
        assert "list" in updated.supported_methods


class TestGetListing:

    def test_get_existing(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        fetched = store.get_listing(created.id)
        assert fetched.id == created.id

    def test_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            store.get_listing("ghost")


class TestSearch:

    def test_text_search_name(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Code Generator"))
        store.register_listing(_make_listing(name="Image Classifier"))

        result = store.search(RegistrySearchQuery(query="code"))
        assert result.total_count == 1
        assert result.listings[0].name == "Code Generator"

    def test_text_search_description(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(name="Agent A", description="Generates Python code")
        )
        store.register_listing(
            _make_listing(name="Agent B", description="Classifies images")
        )

        result = store.search(RegistrySearchQuery(query="python"))
        assert result.total_count == 1
        assert result.listings[0].name == "Agent A"

    def test_category_filter(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Agent"))
        store.register_listing(_make_mcp_listing(name="MCP"))

        agents = store.search(
            RegistrySearchQuery(category=ListingCategory.AGENT)
        )
        assert agents.total_count == 1
        assert agents.listings[0].name == "Agent"

        servers = store.search(
            RegistrySearchQuery(category=ListingCategory.MCP_SERVER)
        )
        assert servers.total_count == 1
        assert servers.listings[0].name == "MCP"

    def test_price_filter(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="Cheap",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=0.001,
            )
        )
        store.register_listing(
            _make_listing(
                name="Expensive",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=1.0,
            )
        )
        store.register_listing(
            _make_listing(name="Free", pricing_model=PricingModel.FREE)
        )

        result = store.search(RegistrySearchQuery(max_price=0.01))
        names = {l.name for l in result.listings}
        assert "Cheap" in names
        assert "Free" in names
        assert "Expensive" not in names

    def test_tag_filter(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Tagged", tags=["ai", "code"]))
        store.register_listing(_make_listing(name="Untagged", tags=["image"]))

        result = store.search(RegistrySearchQuery(tags=["code"]))
        assert result.total_count == 1
        assert result.listings[0].name == "Tagged"

    def test_sort_by_usage(self) -> None:
        store = RegistryStore()
        lo = store.register_listing(_make_listing(name="Low", total_calls=10))
        hi = store.register_listing(_make_listing(name="High", total_calls=1000))

        result = store.search(RegistrySearchQuery(sort_by="usage"))
        assert result.listings[0].name == "High"
        assert result.listings[1].name == "Low"

    def test_sort_by_price_ascending(self) -> None:
        store = RegistryStore()
        store.register_listing(
            _make_listing(
                name="Expensive",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=1.0,
            )
        )
        store.register_listing(
            _make_listing(
                name="Cheap",
                pricing_model=PricingModel.PER_CALL,
                price_per_call=0.001,
            )
        )

        result = store.search(RegistrySearchQuery(sort_by="price"))
        assert result.listings[0].name == "Cheap"

    def test_pagination(self) -> None:
        store = RegistryStore()
        for i in range(5):
            store.register_listing(_make_listing(name=f"Agent-{i}"))

        page1 = store.search(RegistrySearchQuery(limit=2, offset=0))
        assert len(page1.listings) == 2
        assert page1.total_count == 5
        assert page1.has_more is True

        page3 = store.search(RegistrySearchQuery(limit=2, offset=4))
        assert len(page3.listings) == 1
        assert page3.has_more is False

    def test_excludes_deprecated(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing(name="Active"))
        dep = store.register_listing(
            _make_listing(name="ToDeprecate", owner_address="0xdep")
        )
        store.deprecate_listing(dep.id, "0xdep")

        result = store.search(RegistrySearchQuery())
        assert result.total_count == 1
        assert result.listings[0].name == "Active"

    def test_excludes_suspended(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Active"))
        sus = store.register_listing(_make_listing(name="Suspended"))
        store.suspend_listing(sus.id, "violation")

        result = store.search(RegistrySearchQuery())
        assert result.total_count == 1


class TestSuspendListing:

    def test_suspend_changes_status(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        suspended = store.suspend_listing(created.id, "policy violation")
        assert suspended.status == ListingStatus.SUSPENDED

    def test_suspend_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            store.suspend_listing("ghost", "reason")


class TestDeprecateListing:

    def test_deprecate_changes_status(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        deprecated = store.deprecate_listing(created.id, "0xowner1")
        assert deprecated.status == ListingStatus.DEPRECATED

    def test_wrong_owner_raises(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        with pytest.raises(ListingPermissionError):
            store.deprecate_listing(created.id, "0xhacker")

    def test_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            store.deprecate_listing("ghost", "0xowner1")


class TestGetListingsByOwner:

    def test_returns_all_for_owner(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="A1", owner_address="0xalice"))
        store.register_listing(_make_listing(name="A2", owner_address="0xalice"))
        store.register_listing(_make_listing(name="B1", owner_address="0xbob"))

        alice_listings = store.get_listings_by_owner("0xalice")
        assert len(alice_listings) == 2
        names = {l.name for l in alice_listings}
        assert names == {"A1", "A2"}

    def test_returns_empty_for_unknown_owner(self) -> None:
        store = RegistryStore()
        assert store.get_listings_by_owner("0xnobody") == []


class TestGetPopular:

    def test_sorted_by_total_calls(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Low", total_calls=10))
        store.register_listing(_make_listing(name="High", total_calls=1000))
        store.register_listing(_make_listing(name="Mid", total_calls=500))

        popular = store.get_popular(limit=3)
        assert popular[0].name == "High"
        assert popular[1].name == "Mid"
        assert popular[2].name == "Low"

    def test_limit_respected(self) -> None:
        store = RegistryStore()
        for i in range(10):
            store.register_listing(_make_listing(name=f"A-{i}", total_calls=i))

        popular = store.get_popular(limit=3)
        assert len(popular) == 3

    def test_excludes_non_active(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing(name="Active", total_calls=100))
        sus = store.register_listing(
            _make_listing(name="Suspended", total_calls=9999)
        )
        store.suspend_listing(sus.id, "violation")

        popular = store.get_popular()
        assert len(popular) == 1
        assert popular[0].name == "Active"


class TestRecordCall:

    def test_increments_total_calls(self) -> None:
        store = RegistryStore()
        created = store.register_listing(_make_listing())
        assert created.total_calls == 0

        store.record_call(created.id)
        updated = store.get_listing(created.id)
        assert updated.total_calls == 1

    def test_ignores_missing_listing(self) -> None:
        store = RegistryStore()
        # Should not raise
        store.record_call("nonexistent")


class TestReset:

    def test_clears_all(self) -> None:
        store = RegistryStore()
        store.register_listing(_make_listing())
        store.register_listing(_make_listing())
        store.reset()
        assert store.listing_count == 0
