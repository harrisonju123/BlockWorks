"""Tests for framework adapters — request/response transformation."""

from __future__ import annotations

import pytest

from blockthrough.interop.adapters.crewai_adapter import CrewAIAdapter
from blockthrough.interop.adapters.generic_adapter import GenericHTTPAdapter
from blockthrough.interop.adapters.langchain_adapter import LangChainAdapter
from blockthrough.interop.types import InvocationRequest, InvocationStatus


def _make_request(**overrides) -> InvocationRequest:
    defaults = dict(
        caller_agent_id="agent-a",
        target_listing_id="listing-b",
        method="search",
        params={"query": "test"},
    )
    defaults.update(overrides)
    return InvocationRequest(**defaults)


# ---------------------------------------------------------------------------
# LangChain adapter
# ---------------------------------------------------------------------------


class TestLangChainAdapter:

    @pytest.mark.asyncio
    async def test_invoke_returns_success(self) -> None:
        adapter = LangChainAdapter()
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000")

        assert response.status == InvocationStatus.SUCCESS
        assert response.target_framework == "langchain"
        assert response.result["stub"] is True
        assert response.result["framework"] == "langchain"

    @pytest.mark.asyncio
    async def test_invoke_includes_tool_call(self) -> None:
        adapter = LangChainAdapter()
        request = _make_request(method="generate")
        response = await adapter.invoke(request, "http://localhost:8000")

        tool_call = response.result["tool_call"]
        assert tool_call["name"] == "generate"
        assert tool_call["args"] == {"query": "test"}
        assert tool_call["type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_invoke_records_latency(self) -> None:
        adapter = LangChainAdapter()
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000")

        assert response.latency_ms >= 0.0

    def test_framework_name(self) -> None:
        assert LangChainAdapter().framework_name == "langchain"

    def test_get_capabilities_empty(self) -> None:
        """Stub adapter returns no capabilities."""
        assert LangChainAdapter().get_capabilities() == []


# ---------------------------------------------------------------------------
# CrewAI adapter
# ---------------------------------------------------------------------------


class TestCrewAIAdapter:

    @pytest.mark.asyncio
    async def test_invoke_returns_success(self) -> None:
        adapter = CrewAIAdapter()
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000")

        assert response.status == InvocationStatus.SUCCESS
        assert response.target_framework == "crewai"
        assert response.result["stub"] is True
        assert response.result["framework"] == "crewai"

    @pytest.mark.asyncio
    async def test_invoke_includes_task(self) -> None:
        adapter = CrewAIAdapter()
        request = _make_request(method="analyze")
        response = await adapter.invoke(request, "http://localhost:8000")

        task = response.result["task"]
        assert "analyze" in task["description"]
        assert task["context"] == {"query": "test"}
        assert task["agent_role"] == "analyze"

    @pytest.mark.asyncio
    async def test_invoke_records_latency(self) -> None:
        adapter = CrewAIAdapter()
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000")

        assert response.latency_ms >= 0.0

    def test_framework_name(self) -> None:
        assert CrewAIAdapter().framework_name == "crewai"

    def test_get_capabilities_empty(self) -> None:
        assert CrewAIAdapter().get_capabilities() == []


# ---------------------------------------------------------------------------
# Generic HTTP adapter
# ---------------------------------------------------------------------------


class TestGenericHTTPAdapter:

    def test_framework_name(self) -> None:
        assert GenericHTTPAdapter().framework_name == "generic"

    def test_get_capabilities_empty(self) -> None:
        assert GenericHTTPAdapter().get_capabilities() == []

    @pytest.mark.asyncio
    async def test_invoke_timeout(self) -> None:
        """Connecting to a non-routable IP triggers timeout."""
        import httpx

        # Use a mock client that always raises TimeoutException
        class TimeoutClient(httpx.AsyncClient):
            async def post(self, *args, **kwargs):
                raise httpx.TimeoutException("timed out")

        adapter = GenericHTTPAdapter(client=TimeoutClient())
        request = _make_request(timeout_s=1)
        response = await adapter.invoke(request, "http://192.0.2.1:9999")

        assert response.status == InvocationStatus.TIMEOUT
        assert "timed out" in response.result["error"]

    @pytest.mark.asyncio
    async def test_invoke_connection_error(self) -> None:
        """Connection errors are handled gracefully."""
        import httpx

        class ErrorClient(httpx.AsyncClient):
            async def post(self, *args, **kwargs):
                raise httpx.ConnectError("refused")

        adapter = GenericHTTPAdapter(client=ErrorClient())
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:99999")

        assert response.status == InvocationStatus.FAILURE
        assert "refused" in response.result["error"]

    @pytest.mark.asyncio
    async def test_invoke_http_error_status(self) -> None:
        """HTTP 4xx/5xx responses map to FAILURE status."""
        import httpx

        class Error500Client(httpx.AsyncClient):
            async def post(self, *args, **kwargs):
                return httpx.Response(
                    status_code=500,
                    text="Internal Server Error",
                    request=httpx.Request("POST", args[0] if args else kwargs.get("url", "")),
                )

        adapter = GenericHTTPAdapter(client=Error500Client())
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000/fail")

        assert response.status == InvocationStatus.FAILURE
        assert response.result["status_code"] == 500

    @pytest.mark.asyncio
    async def test_invoke_success(self) -> None:
        """Successful HTTP response maps to SUCCESS status."""
        import httpx

        class SuccessClient(httpx.AsyncClient):
            async def post(self, *args, **kwargs):
                return httpx.Response(
                    status_code=200,
                    json={"output": "hello"},
                    request=httpx.Request("POST", args[0] if args else kwargs.get("url", "")),
                )

        adapter = GenericHTTPAdapter(client=SuccessClient())
        request = _make_request()
        response = await adapter.invoke(request, "http://localhost:8000/ok")

        assert response.status == InvocationStatus.SUCCESS
        assert response.result["output"] == "hello"
        assert response.target_framework == "generic"
        assert response.latency_ms >= 0.0
