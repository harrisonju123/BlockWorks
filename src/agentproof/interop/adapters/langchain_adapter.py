"""LangChain framework adapter stub.

Formats invocations as LangChain tool calls. Real framework
integration is future work — this stub validates the adapter
interface and provides the request/response transformation logic.
"""

from __future__ import annotations

import time
import uuid

from agentproof.interop.adapters.base import FrameworkAdapter
from agentproof.interop.types import (
    AgentCapability,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
)


class LangChainAdapter(FrameworkAdapter):
    """Stub adapter that formats invocations as LangChain tool calls."""

    @property
    def framework_name(self) -> str:
        return "langchain"

    async def invoke(self, request: InvocationRequest, endpoint_url: str) -> InvocationResponse:
        """Transform request into LangChain tool-call format and return a stub response.

        In production this would POST a LangChain-formatted payload to the
        endpoint. For now, returns a stub response so the protocol layer
        can be validated end-to-end.
        """
        start = time.monotonic()

        # Build the LangChain tool-call payload shape — proves the
        # transformation logic even though we don't call the endpoint yet
        _tool_call_payload = {
            "name": request.method,
            "args": request.params,
            "id": str(uuid.uuid4()),
            "type": "tool_call",
        }

        elapsed_ms = (time.monotonic() - start) * 1000

        return InvocationResponse(
            request_id=str(uuid.uuid4()),
            status=InvocationStatus.SUCCESS,
            result={
                "framework": "langchain",
                "stub": True,
                "method": request.method,
                "tool_call": _tool_call_payload,
            },
            cost=0.0,
            latency_ms=elapsed_ms,
            target_framework="langchain",
        )

    def get_capabilities(self) -> list[AgentCapability]:
        """No real capabilities until framework integration is built."""
        return []
