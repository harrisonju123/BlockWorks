"""Framework adapters for cross-platform agent invocation.

Each adapter transforms InvocationRequest into a framework's native
call format and maps the result back to InvocationResponse.
"""

from agentproof.interop.adapters.base import FrameworkAdapter
from agentproof.interop.adapters.crewai_adapter import CrewAIAdapter
from agentproof.interop.adapters.generic_adapter import GenericHTTPAdapter
from agentproof.interop.adapters.langchain_adapter import LangChainAdapter

__all__ = [
    "CrewAIAdapter",
    "FrameworkAdapter",
    "GenericHTTPAdapter",
    "LangChainAdapter",
]
