"""CrewAI framework adapter stub.

Formats invocations as CrewAI agent tasks. Real framework
integration is future work — this stub validates the adapter
interface and provides the request/response transformation logic.
"""

from __future__ import annotations

import time
import uuid

from blockthrough.interop.adapters.base import FrameworkAdapter
from blockthrough.interop.types import (
    AgentCapability,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
)


class CrewAIAdapter(FrameworkAdapter):
    """Stub adapter that formats invocations as CrewAI agent tasks."""

    @property
    def framework_name(self) -> str:
        return "crewai"

    async def invoke(self, request: InvocationRequest, endpoint_url: str) -> InvocationResponse:
        """Transform request into CrewAI task format and return a stub response.

        In production this would submit a CrewAI Task to a crew endpoint.
        For now, returns a stub response showing the transformation.
        """
        start = time.monotonic()

        # Build the CrewAI task payload shape
        _task_payload = {
            "description": f"Execute {request.method}",
            "expected_output": "JSON result",
            "agent_role": request.method,
            "context": request.params,
        }

        elapsed_ms = (time.monotonic() - start) * 1000

        return InvocationResponse(
            request_id=str(uuid.uuid4()),
            status=InvocationStatus.SUCCESS,
            result={
                "framework": "crewai",
                "stub": True,
                "method": request.method,
                "task": _task_payload,
            },
            cost=0.0,
            latency_ms=elapsed_ms,
            target_framework="crewai",
        )

    def get_capabilities(self) -> list[AgentCapability]:
        """No real capabilities until framework integration is built."""
        return []
