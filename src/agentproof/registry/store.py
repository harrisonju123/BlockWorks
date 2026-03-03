"""In-memory registry store for agent and MCP server listings.

Follows the same singleton pattern as TrustRegistry and ChannelManager.
Production would persist to TimescaleDB; this in-memory implementation
validates the interface and supports local-first development.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from agentproof.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    PricingModel,
    RegistrySearchQuery,
    RegistrySearchResult,
)
from agentproof.trust.registry import AgentNotRegisteredError, TrustRegistry
from agentproof.utils import utcnow


class ListingNotFoundError(Exception):
    pass


class ListingPermissionError(Exception):
    """Raised when a non-owner tries to modify a listing."""

    pass


class InsufficientStakeError(Exception):
    pass


class RegistryStore:
    """In-memory registry with search, filtering, and trust integration."""

    def __init__(
        self,
        trust_registry: TrustRegistry | None = None,
        min_stake: float = 0.01,
    ) -> None:
        self._listings: dict[str, AgentListing] = {}
        self._trust_registry = trust_registry
        self._min_stake = min_stake

    @property
    def listing_count(self) -> int:
        return len(self._listings)

    def register_listing(self, listing: AgentListing) -> AgentListing:
        """Register a new listing. Assigns an ID and validates stake.

        Raises:
            InsufficientStakeError: If stake_amount < minimum threshold.
        """
        if listing.stake_amount < self._min_stake:
            raise InsufficientStakeError(
                f"Minimum stake is {self._min_stake}, got {listing.stake_amount}"
            )

        now = utcnow()
        listing_id = str(uuid.uuid4())

        # Build the stored listing with generated fields
        data = listing.model_dump()
        data["id"] = listing_id
        data["registered_at"] = now
        data["last_active"] = now
        data["status"] = ListingStatus.ACTIVE

        # Pull live trust score if a trust registry is wired up;
        # otherwise keep whatever the listing already has
        if self._trust_registry is not None:
            data["trust_score"] = self._get_live_trust_score(listing.owner_address)

        # Preserve subclass type
        if isinstance(listing, MCPServerListing):
            stored = MCPServerListing(**data)
        else:
            stored = AgentListing(**data)

        self._listings[listing_id] = stored
        return stored

    def update_listing(
        self, listing_id: str, owner_address: str, updates: dict
    ) -> AgentListing:
        """Update a listing. Only the owner can update.

        Raises:
            ListingNotFoundError: If listing_id does not exist.
            ListingPermissionError: If caller is not the owner.
        """
        existing = self._listings.get(listing_id)
        if existing is None:
            raise ListingNotFoundError(f"Listing {listing_id} not found")

        if existing.owner_address != owner_address:
            raise ListingPermissionError("Only the listing owner can update")

        # Allowlist of updatable fields — prevent ID/owner/status tampering
        allowed = {
            "name",
            "description",
            "pricing_model",
            "price_per_call",
            "tags",
            "endpoint_url",
            "supported_methods",
            "avg_latency_ms",
            "failure_rate",
            "response_token_avg",
        }

        data = existing.model_dump()
        for key, value in updates.items():
            if key in allowed:
                data[key] = value

        data["last_active"] = utcnow()

        if isinstance(existing, MCPServerListing):
            updated = MCPServerListing(**data)
        else:
            updated = AgentListing(**data)

        self._listings[listing_id] = updated
        return updated

    def get_listing(self, listing_id: str) -> AgentListing:
        """Get a listing by ID.

        Raises:
            ListingNotFoundError: If listing_id does not exist.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            raise ListingNotFoundError(f"Listing {listing_id} not found")
        return listing

    def search(self, query: RegistrySearchQuery) -> RegistrySearchResult:
        """Search listings with text, filters, and sorting."""
        results = [
            l
            for l in self._listings.values()
            if l.status in (ListingStatus.ACTIVE, ListingStatus.PENDING)
        ]

        # Text search on name and description
        if query.query:
            q_lower = query.query.lower()
            results = [
                l
                for l in results
                if q_lower in l.name.lower() or q_lower in l.description.lower()
            ]

        # Category filter
        if query.category is not None:
            results = [l for l in results if l.category == query.category]

        # Trust score floor
        if query.min_trust_score is not None:
            # Refresh trust scores before filtering
            results = [
                self._with_live_trust(l) for l in results
            ]
            results = [
                l for l in results if l.trust_score >= query.min_trust_score
            ]

        # Price ceiling
        if query.max_price is not None:
            results = [
                l
                for l in results
                if l.pricing_model == PricingModel.FREE
                or l.price_per_call <= query.max_price
            ]

        # Tag filter (any match)
        if query.tags:
            tag_set = set(query.tags)
            results = [
                l for l in results if tag_set.intersection(set(l.tags))
            ]

        # Sort
        sort_key = _SORT_KEYS.get(query.sort_by, _sort_by_trust)
        results.sort(key=sort_key, reverse=(query.sort_by != "price"))

        total = len(results)
        page = results[query.offset : query.offset + query.limit]

        return RegistrySearchResult(
            listings=page,
            total_count=total,
            has_more=(query.offset + query.limit) < total,
        )

    def suspend_listing(self, listing_id: str, reason: str) -> AgentListing:
        """Admin action: suspend a listing.

        Raises:
            ListingNotFoundError: If listing_id does not exist.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            raise ListingNotFoundError(f"Listing {listing_id} not found")

        data = listing.model_dump()
        data["status"] = ListingStatus.SUSPENDED

        if isinstance(listing, MCPServerListing):
            updated = MCPServerListing(**data)
        else:
            updated = AgentListing(**data)

        self._listings[listing_id] = updated
        return updated

    def deprecate_listing(self, listing_id: str, owner_address: str) -> AgentListing:
        """Owner soft-deletes a listing by marking it deprecated.

        Raises:
            ListingNotFoundError: If listing_id does not exist.
            ListingPermissionError: If caller is not the owner.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            raise ListingNotFoundError(f"Listing {listing_id} not found")

        if listing.owner_address != owner_address:
            raise ListingPermissionError("Only the listing owner can deprecate")

        data = listing.model_dump()
        data["status"] = ListingStatus.DEPRECATED

        if isinstance(listing, MCPServerListing):
            updated = MCPServerListing(**data)
        else:
            updated = AgentListing(**data)

        self._listings[listing_id] = updated
        return updated

    def get_listings_by_owner(self, owner_address: str) -> list[AgentListing]:
        """Get all listings owned by an address."""
        return [
            l
            for l in self._listings.values()
            if l.owner_address == owner_address
        ]

    def get_popular(self, limit: int = 10) -> list[AgentListing]:
        """Get the most-used active listings by total_calls."""
        active = [
            l for l in self._listings.values() if l.status == ListingStatus.ACTIVE
        ]
        active.sort(key=lambda l: l.total_calls, reverse=True)
        return active[:limit]

    def record_call(self, listing_id: str) -> None:
        """Increment total_calls and update last_active. Ignores missing listings."""
        listing = self._listings.get(listing_id)
        if listing is None:
            return

        self._listings[listing_id] = listing.model_copy(
            update={"total_calls": listing.total_calls + 1, "last_active": utcnow()}
        )

    def reset(self) -> None:
        """Clear all state. Used by tests."""
        self._listings.clear()

    # -- Private helpers --

    def _get_live_trust_score(self, agent_id: str) -> float:
        """Pull composite trust score from the trust registry, or 0.0 if unavailable."""
        if self._trust_registry is None:
            return 0.0
        try:
            return self._trust_registry.get_score(agent_id).composite_score
        except AgentNotRegisteredError:
            return 0.0

    def _with_live_trust(self, listing: AgentListing) -> AgentListing:
        """Return a copy of listing with refreshed trust score."""
        live_score = self._get_live_trust_score(listing.owner_address)
        if abs(live_score - listing.trust_score) < 1e-9:
            return listing

        data = listing.model_dump()
        data["trust_score"] = live_score
        if isinstance(listing, MCPServerListing):
            return MCPServerListing(**data)
        return AgentListing(**data)


# -- Sort key functions --

def _sort_by_trust(l: AgentListing) -> float:
    return l.trust_score


def _sort_by_price(l: AgentListing) -> float:
    return l.price_per_call


def _sort_by_usage(l: AgentListing) -> int:
    return l.total_calls


def _sort_by_newest(l: AgentListing) -> datetime:
    return l.registered_at


_SORT_KEYS = {
    "trust": _sort_by_trust,
    "price": _sort_by_price,
    "usage": _sort_by_usage,
    "newest": _sort_by_newest,
}
