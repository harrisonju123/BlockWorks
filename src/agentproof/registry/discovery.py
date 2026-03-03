"""Discovery layer for finding the best agent or MCP server for a task.

Combines trust scores, benchmark data, and pricing to recommend
optimal listings. Intentionally simple scoring -- future versions
may use ML-based ranking.
"""

from __future__ import annotations

from agentproof.registry.store import RegistryStore
from agentproof.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    PricingModel,
)


def find_best_agent(
    store: RegistryStore,
    task_type: str,
    max_price: float | None = None,
    min_quality: float | None = None,
) -> AgentListing | None:
    """Find the best agent for a task type, considering trust, quality, and price.

    Scoring formula: trust_score * 0.4 + benchmark_quality * 0.4 + affordability * 0.2
    where affordability = 1.0 for free, or (max_price - price) / max_price for paid.

    Returns None if no matching agents exist.
    """
    candidates: list[tuple[float, AgentListing]] = []

    for listing in store._listings.values():
        if listing.status != ListingStatus.ACTIVE:
            continue
        if listing.category != ListingCategory.AGENT:
            continue

        # Price filter
        if max_price is not None and listing.pricing_model != PricingModel.FREE:
            if listing.price_per_call > max_price:
                continue

        # Quality filter from benchmark data
        quality = listing.benchmark_performance.get(task_type, 0.0)
        if min_quality is not None and quality < min_quality:
            continue

        # Composite ranking score
        affordability = 1.0
        if max_price is not None and max_price > 0 and listing.pricing_model != PricingModel.FREE:
            affordability = max(0.0, (max_price - listing.price_per_call) / max_price)

        score = (
            listing.trust_score * 0.4
            + quality * 0.4
            + affordability * 0.2
        )
        candidates.append((score, listing))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def find_compatible_mcp_servers(
    store: RegistryStore,
    agent_id: str,
) -> list[MCPServerListing]:
    """Find MCP servers that are compatible with a given agent.

    For now, "compatible" means active MCP_SERVER listings. Future
    versions will check method compatibility and protocol versions.
    """
    # Verify the agent exists (we don't error if not found -- just return empty)
    agent = store._listings.get(agent_id)
    if agent is None:
        return []

    return [
        listing
        for listing in store._listings.values()
        if (
            isinstance(listing, MCPServerListing)
            and listing.category == ListingCategory.MCP_SERVER
            and listing.status == ListingStatus.ACTIVE
        )
    ]


def get_recommendations(
    store: RegistryStore,
    user_history: list[str],
    limit: int = 5,
) -> list[AgentListing]:
    """Suggest agents based on past usage patterns.

    Simple collaborative approach: look at tags from previously used
    listings and recommend highly-trusted active listings with
    overlapping tags. Falls back to popular listings if no tag overlap.

    Args:
        user_history: List of listing IDs the user has previously used.
        limit: Maximum number of recommendations.
    """
    # Collect tags from user's history
    used_tags: set[str] = set()
    used_ids: set[str] = set(user_history)

    for lid in user_history:
        listing = store._listings.get(lid)
        if listing is not None:
            used_tags.update(listing.tags)

    # Score active listings by tag overlap and trust
    candidates: list[tuple[float, AgentListing]] = []

    for listing in store._listings.values():
        if listing.status != ListingStatus.ACTIVE:
            continue
        if listing.id in used_ids:
            continue

        tag_overlap = len(used_tags.intersection(set(listing.tags)))
        # Weighted: tag_overlap drives relevance, trust breaks ties
        score = tag_overlap * 10.0 + listing.trust_score
        candidates.append((score, listing))

    candidates.sort(key=lambda t: t[0], reverse=True)

    # If no tag-based recommendations, fall back to popular
    if not candidates or candidates[0][0] <= 0:
        popular = store.get_popular(limit=limit)
        return [p for p in popular if p.id not in used_ids][:limit]

    return [c[1] for c in candidates[:limit]]
