"""Discovery bridge — connects registry lookup with the invocation protocol.

Translates capability queries into registry searches, resolves listing
IDs to endpoints + framework types, and negotiates payment channels
for cross-agent transactions.
"""

from __future__ import annotations

from blockthrough.channels.manager import ChannelManager
from blockthrough.interop.adapters.base import FrameworkAdapter
from blockthrough.interop.adapters.crewai_adapter import CrewAIAdapter
from blockthrough.interop.adapters.generic_adapter import GenericHTTPAdapter
from blockthrough.interop.adapters.langchain_adapter import LangChainAdapter
from blockthrough.interop.types import AgentCapability
from blockthrough.registry.store import ListingNotFoundError, RegistryStore
from blockthrough.registry.types import (
    AgentListing,
    ListingCategory,
    ListingStatus,
    MCPServerListing,
    RegistrySearchQuery,
)


class EndpointResolution:
    """Result of resolving a listing to its network endpoint."""

    def __init__(self, endpoint_url: str, framework: str, listing: AgentListing) -> None:
        self.endpoint_url = endpoint_url
        self.framework = framework
        self.listing = listing


class DiscoveryBridge:
    """Bridges registry discovery with the invocation protocol.

    Provides capability-based agent discovery, endpoint resolution,
    and payment channel negotiation.
    """

    # Maps framework tag strings to adapter classes — adapters are
    # instantiated lazily so we don't import framework SDKs at module
    # load time
    FRAMEWORK_ADAPTERS: dict[str, type[FrameworkAdapter]] = {
        "langchain": LangChainAdapter,
        "crewai": CrewAIAdapter,
        "generic": GenericHTTPAdapter,
    }

    def __init__(
        self,
        registry: RegistryStore,
        channel_manager: ChannelManager | None = None,
    ) -> None:
        self._registry = registry
        self._channel_manager = channel_manager
        self._adapter_cache: dict[str, FrameworkAdapter] = {}

    def discover_agents(self, capability_query: str) -> list[AgentCapability]:
        """Find agents matching a capability query string.

        Searches registry by name/description and tags, then builds
        AgentCapability descriptors from the matching listings.
        """
        result = self._registry.search(
            RegistrySearchQuery(query=capability_query)
        )

        capabilities: list[AgentCapability] = []
        for listing in result.listings:
            methods = []
            if isinstance(listing, MCPServerListing):
                methods = listing.supported_methods

            # Infer supported frameworks from tags
            frameworks = [
                tag
                for tag in listing.tags
                if tag in self.FRAMEWORK_ADAPTERS
            ]
            if not frameworks:
                frameworks = ["generic"]

            capabilities.append(
                AgentCapability(
                    listing_id=listing.id,
                    methods=methods,
                    input_schema={},
                    output_schema={},
                    supported_frameworks=frameworks,
                )
            )

        return capabilities

    def resolve_endpoint(self, listing_id: str) -> EndpointResolution:
        """Resolve a listing ID to its endpoint URL and framework type.

        Raises:
            ListingNotFoundError: If the listing_id does not exist.
            ValueError: If the listing has no endpoint configured.
        """
        listing = self._registry.get_listing(listing_id)

        if not listing.endpoint_url:
            raise ValueError(
                f"Listing {listing_id} has no endpoint_url configured"
            )

        # Determine framework from tags, falling back to generic
        framework = "generic"
        for tag in listing.tags:
            if tag in self.FRAMEWORK_ADAPTERS:
                framework = tag
                break

        return EndpointResolution(
            endpoint_url=listing.endpoint_url,
            framework=framework,
            listing=listing,
        )

    def get_adapter(self, framework: str) -> FrameworkAdapter:
        """Get or create the adapter for a framework.

        Raises:
            ValueError: If the framework is not recognized.
        """
        if framework in self._adapter_cache:
            return self._adapter_cache[framework]

        adapter_cls = self.FRAMEWORK_ADAPTERS.get(framework)
        if adapter_cls is None:
            raise ValueError(
                f"Unknown framework '{framework}'. "
                f"Supported: {list(self.FRAMEWORK_ADAPTERS.keys())}"
            )

        adapter = adapter_cls()
        self._adapter_cache[framework] = adapter
        return adapter

    def negotiate_payment(
        self,
        caller_id: str,
        target_id: str,
        estimated_cost: float,
    ) -> str | None:
        """Open a payment channel between caller and target.

        Returns the channel_id if a ChannelManager is configured,
        None otherwise.
        """
        if self._channel_manager is None:
            return None

        state = self._channel_manager.open_channel(
            sender=caller_id,
            receiver=target_id,
            deposit=estimated_cost,
        )
        return state.channel_id

    def reset(self) -> None:
        """Clear cached adapters. Used by tests."""
        self._adapter_cache.clear()
