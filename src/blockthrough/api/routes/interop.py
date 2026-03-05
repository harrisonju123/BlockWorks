"""Interoperability API endpoints — invoke, discover, capabilities, disputes.

Backed by in-memory stores for local development. Provides the HTTP
surface for the cross-platform agent invocation protocol.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from blockthrough.config import get_config
from blockthrough.interop.bridge import DiscoveryBridge
from blockthrough.interop.metering import (
    DisputeAlreadyResolvedError,
    DisputeNotFoundError,
    MeteringStore,
)
from blockthrough.interop.protocol import create_invocation, validate_message, verify_message_signature
from blockthrough.interop.types import (
    AgentCapability,
    DisputeRecord,
    DisputeStatus,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
)
from blockthrough.registry.store import ListingNotFoundError, RegistryStore
from blockthrough.registry.types import MCPServerListing

router = APIRouter(prefix="/interop")

# ---------------------------------------------------------------------------
# Module-level singletons — lazily initialized on first request
# ---------------------------------------------------------------------------

_bridge: DiscoveryBridge | None = None
_metering: MeteringStore | None = None
_registry: RegistryStore | None = None


def _get_registry() -> RegistryStore:
    global _registry
    if _registry is None:
        cfg = get_config()
        _registry = RegistryStore(min_stake=cfg.registry_min_stake)
    return _registry


def _get_bridge() -> DiscoveryBridge:
    global _bridge
    if _bridge is None:
        _bridge = DiscoveryBridge(registry=_get_registry())
    return _bridge


def _get_metering() -> MeteringStore:
    global _metering
    if _metering is None:
        _metering = MeteringStore()
    return _metering


def reset_stores() -> None:
    """Reset module-level singletons. Used by tests."""
    global _bridge, _metering, _registry
    _bridge = None
    _metering = None
    _registry = None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class InvokeRequest(BaseModel):
    caller_agent_id: str
    target_listing_id: str
    method: str
    params: dict = Field(default_factory=dict)
    max_cost: float = 1.0
    timeout_s: int = 30
    trace_id: str = ""


class InvokeResponse(BaseModel):
    request_id: str
    status: str
    result: dict
    cost: float
    latency_ms: float
    target_framework: str


class DiscoverRequest(BaseModel):
    capability_query: str


class CapabilityResponse(BaseModel):
    listing_id: str
    methods: list[str]
    input_schema: dict
    output_schema: dict
    supported_frameworks: list[str]


class DiscoverResponse(BaseModel):
    capabilities: list[CapabilityResponse]


class OpenDisputeRequest(BaseModel):
    invocation_id: str
    initiator: str
    reason: str
    evidence_hash: str


class ResolveDisputeRequest(BaseModel):
    resolution: str
    resolver: str


class DisputeResponse(BaseModel):
    id: str
    invocation_id: str
    initiator: str
    reason: str
    evidence_hash: str
    status: str
    resolution: str
    resolver: str
    opened_at: str
    resolved_at: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dispute_to_response(dispute: DisputeRecord) -> DisputeResponse:
    return DisputeResponse(
        id=dispute.id,
        invocation_id=dispute.invocation_id,
        initiator=dispute.initiator,
        reason=dispute.reason,
        evidence_hash=dispute.evidence_hash,
        status=dispute.status.value,
        resolution=dispute.resolution,
        resolver=dispute.resolver,
        opened_at=dispute.opened_at.isoformat(),
        resolved_at=dispute.resolved_at.isoformat() if dispute.resolved_at else None,
    )


def _capability_to_response(cap: AgentCapability) -> CapabilityResponse:
    return CapabilityResponse(
        listing_id=cap.listing_id,
        methods=cap.methods,
        input_schema=cap.input_schema,
        output_schema=cap.output_schema,
        supported_frameworks=cap.supported_frameworks,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/invoke", response_model=InvokeResponse)
async def invoke_agent(body: InvokeRequest) -> InvokeResponse:
    """Invoke an agent or MCP server through the interop protocol.

    Resolves the target listing, selects the appropriate framework
    adapter, executes the invocation, and meters the result.
    """
    cfg = get_config()
    if not cfg.interop_enabled:
        raise HTTPException(status_code=503, detail="Interop protocol is disabled")

    if body.max_cost > cfg.interop_max_cost_per_invocation:
        raise HTTPException(
            status_code=422,
            detail=f"max_cost {body.max_cost} exceeds limit {cfg.interop_max_cost_per_invocation}",
        )

    bridge = _get_bridge()
    metering = _get_metering()

    # Resolve target endpoint and framework
    try:
        resolution = bridge.resolve_endpoint(body.target_listing_id)
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail="Target listing not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Get the adapter for the target framework
    try:
        adapter = bridge.get_adapter(resolution.framework)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Build the invocation request
    request = InvocationRequest(
        caller_agent_id=body.caller_agent_id,
        target_listing_id=body.target_listing_id,
        method=body.method,
        params=body.params,
        max_cost=body.max_cost,
        timeout_s=body.timeout_s or cfg.interop_default_timeout_s,
        trace_id=body.trace_id,
    )

    # Execute through the adapter
    response = await adapter.invoke(request, resolution.endpoint_url)

    # Meter the invocation
    metering.meter_invocation(request, response)

    # Record the call in the registry
    bridge._registry.record_call(body.target_listing_id)

    return InvokeResponse(
        request_id=response.request_id,
        status=response.status.value,
        result=response.result,
        cost=response.cost,
        latency_ms=response.latency_ms,
        target_framework=response.target_framework,
    )


@router.get("/capabilities/{listing_id}", response_model=CapabilityResponse)
async def get_capabilities(listing_id: str) -> CapabilityResponse:
    """Get the capabilities of a registered agent or MCP server."""
    bridge = _get_bridge()

    try:
        resolution = bridge.resolve_endpoint(listing_id)
    except ListingNotFoundError:
        raise HTTPException(status_code=404, detail="Listing not found")
    except ValueError:
        # No endpoint, but listing exists — build capability from listing data
        try:
            listing = bridge._registry.get_listing(listing_id)
        except ListingNotFoundError:
            raise HTTPException(status_code=404, detail="Listing not found")

        methods = []
        if isinstance(listing, MCPServerListing):
            methods = listing.supported_methods

        frameworks = [t for t in listing.tags if t in bridge.FRAMEWORK_ADAPTERS]
        if not frameworks:
            frameworks = ["generic"]

        cap = AgentCapability(
            listing_id=listing_id,
            methods=methods,
            supported_frameworks=frameworks,
        )
        return _capability_to_response(cap)

    # Listing with endpoint
    listing = resolution.listing
    methods = []
    if isinstance(listing, MCPServerListing):
        methods = listing.supported_methods

    frameworks = [t for t in listing.tags if t in bridge.FRAMEWORK_ADAPTERS]
    if not frameworks:
        frameworks = [resolution.framework]

    cap = AgentCapability(
        listing_id=listing_id,
        methods=methods,
        supported_frameworks=frameworks,
    )
    return _capability_to_response(cap)


@router.post("/discover", response_model=DiscoverResponse)
async def discover_agents(body: DiscoverRequest) -> DiscoverResponse:
    """Discover agents by capability query."""
    bridge = _get_bridge()
    capabilities = bridge.discover_agents(body.capability_query)
    return DiscoverResponse(
        capabilities=[_capability_to_response(c) for c in capabilities],
    )


@router.post("/disputes", response_model=DisputeResponse, status_code=201)
async def open_dispute(body: OpenDisputeRequest) -> DisputeResponse:
    """Open a dispute against a completed invocation."""
    metering = _get_metering()
    dispute = metering.open_dispute(
        invocation_id=body.invocation_id,
        initiator=body.initiator,
        reason=body.reason,
        evidence_hash=body.evidence_hash,
    )
    return _dispute_to_response(dispute)


@router.get("/disputes/{dispute_id}", response_model=DisputeResponse)
async def get_dispute(dispute_id: str) -> DisputeResponse:
    """Get the status of a dispute."""
    metering = _get_metering()
    try:
        dispute = metering.get_dispute(dispute_id)
    except DisputeNotFoundError:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return _dispute_to_response(dispute)


@router.put("/disputes/{dispute_id}/resolve", response_model=DisputeResponse)
async def resolve_dispute(dispute_id: str, body: ResolveDisputeRequest) -> DisputeResponse:
    """Resolve an open dispute."""
    metering = _get_metering()
    try:
        dispute = metering.resolve_dispute(
            dispute_id=dispute_id,
            resolution=body.resolution,
            resolver=body.resolver,
        )
    except DisputeNotFoundError:
        raise HTTPException(status_code=404, detail="Dispute not found")
    except DisputeAlreadyResolvedError:
        raise HTTPException(status_code=409, detail="Dispute is already resolved")
    return _dispute_to_response(dispute)
