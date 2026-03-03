"""Registry API endpoints — CRUD, search, verification, and discovery.

Backed by in-memory RegistryStore for local development.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentproof.config import get_config
from agentproof.registry.discovery import (
    find_best_agent,
    find_compatible_mcp_servers,
    get_recommendations,
)
from agentproof.registry.store import (
    InsufficientStakeError,
    ListingNotFoundError,
    ListingPermissionError,
    RegistryStore,
)
from agentproof.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    PricingModel,
    RegistrySearchQuery,
)
from agentproof.registry.verification import get_verification_status
from agentproof.trust.registry import TrustRegistry
from agentproof.utils import utcnow

router = APIRouter(prefix="/registry")

# ---------------------------------------------------------------------------
# Module-level singletons — lazily initialized on first request
# ---------------------------------------------------------------------------

_store: RegistryStore | None = None
_trust_registry: TrustRegistry | None = None


def _get_store() -> RegistryStore:
    global _store, _trust_registry
    if _store is None:
        cfg = get_config()
        _trust_registry = TrustRegistry()
        _store = RegistryStore(
            trust_registry=_trust_registry,
            min_stake=cfg.registry_min_stake,
        )
    return _store


def _get_trust_registry() -> TrustRegistry | None:
    _get_store()  # ensure initialization
    return _trust_registry


def reset_store() -> None:
    """Reset module-level singletons. Used by tests."""
    global _store, _trust_registry
    _store = None
    _trust_registry = None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateListingRequest(BaseModel):
    name: str
    description: str
    owner_address: str
    category: ListingCategory
    pricing_model: PricingModel = PricingModel.FREE
    price_per_call: float = 0.0
    stake_amount: float = 0.01
    tags: list[str] = Field(default_factory=list)
    endpoint_url: str = ""
    # MCP-specific fields (ignored for agents)
    supported_methods: list[str] = Field(default_factory=list)


class UpdateListingRequest(BaseModel):
    owner_address: str
    name: str | None = None
    description: str | None = None
    pricing_model: PricingModel | None = None
    price_per_call: float | None = None
    tags: list[str] | None = None
    endpoint_url: str | None = None
    supported_methods: list[str] | None = None


class DeprecateRequest(BaseModel):
    owner_address: str


class ListingResponse(BaseModel):
    id: str
    name: str
    description: str
    owner_address: str
    category: str
    pricing_model: str
    price_per_call: float
    trust_score: float
    benchmark_performance: dict[str, float]
    uptime_pct: float
    total_calls: int
    registered_at: str
    last_active: str
    stake_amount: float
    is_verified: bool
    tags: list[str]
    endpoint_url: str
    status: str


class ListingListResponse(BaseModel):
    listings: list[ListingResponse]
    total_count: int
    has_more: bool


class VerificationResponse(BaseModel):
    is_verified: bool
    trust: dict
    uptime: dict
    calls: dict
    stake: dict


class DiscoverResponse(BaseModel):
    listing: ListingResponse | None


class PopularResponse(BaseModel):
    listings: list[ListingResponse]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _listing_to_response(listing: AgentListing) -> ListingResponse:
    return ListingResponse(
        id=listing.id,
        name=listing.name,
        description=listing.description,
        owner_address=listing.owner_address,
        category=listing.category.value,
        pricing_model=listing.pricing_model.value,
        price_per_call=listing.price_per_call,
        trust_score=listing.trust_score,
        benchmark_performance=listing.benchmark_performance,
        uptime_pct=listing.uptime_pct,
        total_calls=listing.total_calls,
        registered_at=listing.registered_at.isoformat(),
        last_active=listing.last_active.isoformat(),
        stake_amount=listing.stake_amount,
        is_verified=listing.is_verified,
        tags=listing.tags,
        endpoint_url=listing.endpoint_url,
        status=listing.status.value,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/listings", response_model=ListingResponse, status_code=201)
async def create_listing(body: CreateListingRequest) -> ListingResponse:
    """Register a new agent or MCP server listing."""
    store = _get_store()

    # Build the appropriate listing type — ID and timestamps are
    # overwritten by store.register_listing(), placeholders here
    now = utcnow()

    if body.category == ListingCategory.MCP_SERVER:
        listing = MCPServerListing(
            id="",
            name=body.name,
            description=body.description,
            owner_address=body.owner_address,
            category=body.category,
            pricing_model=body.pricing_model,
            price_per_call=body.price_per_call,
            stake_amount=body.stake_amount,
            tags=body.tags,
            endpoint_url=body.endpoint_url,
            supported_methods=body.supported_methods,
            registered_at=now,
            last_active=now,
        )
    else:
        listing = AgentListing(
            id="",
            name=body.name,
            description=body.description,
            owner_address=body.owner_address,
            category=body.category,
            pricing_model=body.pricing_model,
            price_per_call=body.price_per_call,
            stake_amount=body.stake_amount,
            tags=body.tags,
            endpoint_url=body.endpoint_url,
            registered_at=now,
            last_active=now,
        )

    try:
        created = store.register_listing(listing)
    except InsufficientStakeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _listing_to_response(created)


@router.get("/listings", response_model=ListingListResponse)
async def search_listings(
    q: str | None = None,
    category: ListingCategory | None = None,
    min_trust_score: float | None = None,
    max_price: float | None = None,
    tags: str | None = None,
    sort_by: str = "trust",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ListingListResponse:
    """Search and list registry entries with filters."""
    store = _get_store()

    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    query = RegistrySearchQuery(
        query=q,
        category=category,
        min_trust_score=min_trust_score,
        max_price=max_price,
        tags=tag_list,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )

    result = store.search(query)
    return ListingListResponse(
        listings=[_listing_to_response(l) for l in result.listings],
        total_count=result.total_count,
        has_more=result.has_more,
    )


@router.get("/popular", response_model=PopularResponse)
async def popular_listings(
    limit: int = Query(default=10, ge=1, le=100),
) -> PopularResponse:
    """Get the most popular listings by usage."""
    store = _get_store()
    popular = store.get_popular(limit=limit)
    return PopularResponse(
        listings=[_listing_to_response(l) for l in popular],
    )


@router.get("/discover", response_model=DiscoverResponse)
async def discover_agent(
    task_type: str,
    max_price: float | None = None,
    min_quality: float | None = None,
) -> DiscoverResponse:
    """Find the best agent for a given task type."""
    store = _get_store()
    best = find_best_agent(store, task_type, max_price, min_quality)
    return DiscoverResponse(
        listing=_listing_to_response(best) if best else None,
    )


@router.get("/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: str) -> ListingResponse:
    """Get a listing by ID."""
    store = _get_store()
    try:
        listing = store.get_listing(listing_id)
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} not found")
    return _listing_to_response(listing)


@router.put("/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(listing_id: str, body: UpdateListingRequest) -> ListingResponse:
    """Update a listing. Only the owner can update."""
    store = _get_store()

    updates = {k: v for k, v in body.model_dump().items() if v is not None and k != "owner_address"}

    try:
        updated = store.update_listing(listing_id, body.owner_address, updates)
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} not found")
    except ListingPermissionError:
        raise HTTPException(status_code=403, detail="Only the listing owner can update")

    return _listing_to_response(updated)


@router.delete("/listings/{listing_id}", response_model=ListingResponse)
async def deprecate_listing(listing_id: str, body: DeprecateRequest) -> ListingResponse:
    """Deprecate (soft-delete) a listing. Only the owner can deprecate."""
    store = _get_store()
    try:
        deprecated = store.deprecate_listing(listing_id, body.owner_address)
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} not found")
    except ListingPermissionError:
        raise HTTPException(status_code=403, detail="Only the listing owner can deprecate")

    return _listing_to_response(deprecated)


@router.get("/listings/{listing_id}/verify", response_model=VerificationResponse)
async def verification_status(listing_id: str) -> VerificationResponse:
    """Get the verification status of a listing."""
    store = _get_store()
    cfg = get_config()
    trust_reg = _get_trust_registry()

    try:
        result = get_verification_status(
            listing_id,
            store,
            trust_reg,
            min_trust=cfg.registry_verification_min_trust,
            min_uptime=cfg.registry_verification_min_uptime * 100,  # config is 0-1, uptime is 0-100
            min_calls=cfg.registry_verification_min_calls,
            min_stake=cfg.registry_min_stake,
        )
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} not found")

    return VerificationResponse(**result.to_dict())
