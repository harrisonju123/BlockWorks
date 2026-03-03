"""Abstract base class for framework-specific invocation adapters.

Each concrete adapter knows how to translate an InvocationRequest
into the native call format of one agent framework and map the
framework's response back to InvocationResponse.
"""

from __future__ import annotations

import abc

from agentproof.interop.types import AgentCapability, InvocationRequest, InvocationResponse


class FrameworkAdapter(abc.ABC):
    """Base class for framework adapters.

    Subclasses implement invoke() to call an agent in the target
    framework, and get_capabilities() to describe what the adapter
    supports.
    """

    @property
    @abc.abstractmethod
    def framework_name(self) -> str:
        """Human-readable name of the target framework (e.g. 'langchain')."""

    @abc.abstractmethod
    async def invoke(self, request: InvocationRequest, endpoint_url: str) -> InvocationResponse:
        """Execute an invocation against the target framework.

        Args:
            request: The cross-framework invocation request.
            endpoint_url: The HTTP endpoint of the target agent.

        Returns:
            InvocationResponse with the result or error status.
        """

    @abc.abstractmethod
    def get_capabilities(self) -> list[AgentCapability]:
        """Describe the adapter's supported methods and schemas.

        Returns an empty list for stubs that don't yet have real
        framework integration.
        """
