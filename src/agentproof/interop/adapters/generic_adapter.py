"""Generic HTTP adapter — the universal fallback for any REST endpoint.

This is the only adapter that actually makes network calls. It POSTs
the InvocationRequest as JSON to the target endpoint and maps the
HTTP response back to InvocationResponse. Works with any agent that
exposes a simple REST API, regardless of framework.
"""

from __future__ import annotations

import time
import uuid

import httpx

from agentproof.interop.adapters.base import FrameworkAdapter
from agentproof.interop.types import (
    AgentCapability,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
)


class GenericHTTPAdapter(FrameworkAdapter):
    """HTTP-based adapter that calls any REST endpoint."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def framework_name(self) -> str:
        return "generic"

    async def invoke(self, request: InvocationRequest, endpoint_url: str) -> InvocationResponse:
        """POST the invocation request to the target endpoint as JSON.

        Maps HTTP status codes and JSON bodies back to InvocationResponse.
        Handles timeouts and connection errors gracefully.
        """
        request_id = str(uuid.uuid4())
        start = time.monotonic()

        body = {
            "method": request.method,
            "params": request.params,
            "caller_agent_id": request.caller_agent_id,
            "trace_id": request.trace_id,
        }

        # Use injected client or create a transient one
        client = self._client
        should_close = False
        if client is None:
            client = httpx.AsyncClient()
            should_close = True

        try:
            resp = await client.post(
                endpoint_url,
                json=body,
                timeout=request.timeout_s,
            )
            elapsed_ms = (time.monotonic() - start) * 1000

            if resp.status_code >= 400:
                return InvocationResponse(
                    request_id=request_id,
                    status=InvocationStatus.FAILURE,
                    result={"error": resp.text, "status_code": resp.status_code},
                    cost=0.0,
                    latency_ms=elapsed_ms,
                    target_framework="generic",
                )

            try:
                result = resp.json()
            except Exception:
                result = {"raw": resp.text}

            return InvocationResponse(
                request_id=request_id,
                status=InvocationStatus.SUCCESS,
                result=result if isinstance(result, dict) else {"data": result},
                cost=0.0,
                latency_ms=elapsed_ms,
                target_framework="generic",
            )

        except httpx.TimeoutException:
            elapsed_ms = (time.monotonic() - start) * 1000
            return InvocationResponse(
                request_id=request_id,
                status=InvocationStatus.TIMEOUT,
                result={"error": "Request timed out"},
                cost=0.0,
                latency_ms=elapsed_ms,
                target_framework="generic",
            )

        except httpx.HTTPError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return InvocationResponse(
                request_id=request_id,
                status=InvocationStatus.FAILURE,
                result={"error": str(exc)},
                cost=0.0,
                latency_ms=elapsed_ms,
                target_framework="generic",
            )

        finally:
            if should_close:
                await client.aclose()

    def get_capabilities(self) -> list[AgentCapability]:
        """Generic adapter supports any method — no fixed capability list."""
        return []
