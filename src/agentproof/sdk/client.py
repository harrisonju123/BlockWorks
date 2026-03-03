"""Async and sync HTTP clients for the AgentProof API.

AgentProofSDK is the primary async client using httpx.AsyncClient.
AgentProofClient is a sync wrapper that creates a one-shot async
event loop per call, suitable for scripts and notebooks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from agentproof.pipeline.hasher import hash_content
from agentproof.utils import utcnow
from agentproof.sdk.types import (
    FitnessEntry,
    FitnessMatrixResponse,
    SDKConfig,
    StatGroup,
    StatsResponse,
    TrackEventRequest,
    TrackEventResponse,
    WasteBreakdownItem,
    WasteScoreResponse,
)

logger = logging.getLogger(__name__)


class AgentProofError(Exception):
    """Raised when the AgentProof API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"AgentProof API error {status_code}: {detail}")


class AgentProofSDK:
    """Async Python SDK for AgentProof.

    Connects to the AgentProof API via httpx.AsyncClient. Handles
    authentication, retries, and response parsing.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8100",
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._config = SDKConfig(
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"

            transport = httpx.AsyncHTTPTransport(retries=self._config.max_retries)
            self._client = httpx.AsyncClient(
                base_url=self._config.api_url,
                headers=headers,
                timeout=self._config.timeout_s,
                transport=transport,
            )
        return self._client

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> AgentProofSDK:
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make an HTTP request and return the parsed JSON response."""
        client = await self._ensure_client()

        # Strip None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        response = await client.request(method, path, json=json, params=params)

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except Exception:
                pass
            raise AgentProofError(response.status_code, str(detail))

        return response.json()

    async def track(
        self,
        model: str,
        messages: list[dict],
        completion: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float,
        latency_ms: float,
        *,
        provider: str = "custom",
        status: str = "success",
        session_id: str | None = None,
        trace_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        metadata: dict | None = None,
    ) -> TrackEventResponse:
        """Report a single LLM call to AgentProof.

        This is the primary method for manual instrumentation — use it
        when you're not going through LiteLLM's callback mechanism.
        """
        event_id = str(uuid.uuid4())
        now = utcnow().isoformat()

        payload = {
            "id": event_id,
            "created_at": now,
            "status": status,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost": estimated_cost,
            "latency_ms": latency_ms,
            "prompt_hash": hash_content(messages),
            "completion_hash": hash_content(completion),
            "trace_id": trace_id or str(uuid.uuid4()),
            "span_id": str(uuid.uuid4()),
            "litellm_call_id": f"sdk-{event_id}",
        }

        if session_id:
            payload["session_id"] = session_id
        if org_id:
            payload["org_id"] = org_id
        if user_id:
            payload["user_id"] = user_id
        if metadata:
            payload["custom_metadata"] = metadata

        data = await self._request("POST", "/api/v1/events/ingest", json=payload)
        return TrackEventResponse(
            event_id=data.get("event_id", event_id),
            created_at=data.get("created_at", now),
        )

    async def get_stats(
        self,
        period: str = "24h",
        *,
        group_by: str = "model",
        org_id: str | None = None,
    ) -> StatsResponse:
        """Fetch summary stats for the given period."""
        params: dict[str, Any] = {"group_by": group_by}
        if org_id:
            params["org_id"] = org_id

        data = await self._request("GET", "/api/v1/stats/summary", params=params)

        groups = [
            StatGroup(
                key=g["key"],
                request_count=g["request_count"],
                total_cost_usd=g["total_cost_usd"],
                avg_latency_ms=g["avg_latency_ms"],
                p95_latency_ms=g["p95_latency_ms"],
                avg_cost_per_request_usd=g["avg_cost_per_request_usd"],
                total_prompt_tokens=g["total_prompt_tokens"],
                total_completion_tokens=g["total_completion_tokens"],
                failure_count=g["failure_count"],
            )
            for g in data.get("groups", [])
        ]

        return StatsResponse(
            total_requests=data["total_requests"],
            total_cost_usd=data["total_cost_usd"],
            total_tokens=data["total_tokens"],
            failure_rate=data["failure_rate"],
            groups=groups,
        )

    async def get_waste_score(
        self,
        *,
        org_id: str | None = None,
    ) -> WasteScoreResponse:
        """Fetch the current waste score."""
        params: dict[str, Any] = {}
        if org_id:
            params["org_id"] = org_id

        data = await self._request("GET", "/api/v1/stats/waste-score", params=params)

        breakdown = [
            WasteBreakdownItem(
                task_type=item["task_type"],
                current_model=item["current_model"],
                suggested_model=item["suggested_model"],
                call_count=item["call_count"],
                current_cost_usd=item["current_cost_usd"],
                projected_cost_usd=item["projected_cost_usd"],
                savings_usd=item["savings_usd"],
                confidence=item["confidence"],
            )
            for item in data.get("breakdown", [])
        ]

        return WasteScoreResponse(
            waste_score=data["waste_score"],
            total_potential_savings_usd=data["total_potential_savings_usd"],
            breakdown=breakdown,
        )

    async def get_fitness_matrix(
        self,
        *,
        org_id: str | None = None,
    ) -> FitnessMatrixResponse:
        """Fetch the fitness matrix from benchmark results."""
        params: dict[str, Any] = {}
        if org_id:
            params["org_id"] = org_id

        data = await self._request(
            "GET", "/api/v1/benchmarks/fitness-matrix", params=params
        )

        entries = [
            FitnessEntry(
                model=e["model"],
                task_type=e["task_type"],
                quality_score=e["quality_score"],
                avg_cost=e["avg_cost"],
                avg_latency_ms=e["avg_latency_ms"],
                sample_count=e["sample_count"],
            )
            for e in data.get("entries", [])
        ]

        return FitnessMatrixResponse(entries=entries)


class AgentProofClient:
    """Synchronous wrapper around AgentProofSDK.

    Creates one-shot event loops for each call. Suitable for scripts,
    notebooks, and applications that don't use asyncio. For async
    applications, use AgentProofSDK directly.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8100",
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._config = SDKConfig(
            api_url=api_url,
            api_key=api_key,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
        self._sync_client: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        if self._sync_client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"

            transport = httpx.HTTPTransport(retries=self._config.max_retries)
            self._sync_client = httpx.Client(
                base_url=self._config.api_url,
                headers=headers,
                timeout=self._config.timeout_s,
                transport=transport,
            )
        return self._sync_client

    def close(self) -> None:
        """Shut down the underlying HTTP client."""
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    def __enter__(self) -> AgentProofClient:
        self._ensure_client()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make an HTTP request and return the parsed JSON response."""
        client = self._ensure_client()

        if params:
            params = {k: v for k, v in params.items() if v is not None}

        response = client.request(method, path, json=json, params=params)

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except Exception:
                pass
            raise AgentProofError(response.status_code, str(detail))

        return response.json()

    def track(
        self,
        model: str,
        messages: list[dict],
        completion: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float,
        latency_ms: float,
        *,
        provider: str = "custom",
        status: str = "success",
        session_id: str | None = None,
        trace_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        metadata: dict | None = None,
    ) -> TrackEventResponse:
        """Report a single LLM call to AgentProof (synchronous)."""
        event_id = str(uuid.uuid4())
        now = utcnow().isoformat()

        payload = {
            "id": event_id,
            "created_at": now,
            "status": status,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost": estimated_cost,
            "latency_ms": latency_ms,
            "prompt_hash": hash_content(messages),
            "completion_hash": hash_content(completion),
            "trace_id": trace_id or str(uuid.uuid4()),
            "span_id": str(uuid.uuid4()),
            "litellm_call_id": f"sdk-{event_id}",
        }

        if session_id:
            payload["session_id"] = session_id
        if org_id:
            payload["org_id"] = org_id
        if user_id:
            payload["user_id"] = user_id
        if metadata:
            payload["custom_metadata"] = metadata

        data = self._request("POST", "/api/v1/events/ingest", json=payload)
        return TrackEventResponse(
            event_id=data.get("event_id", event_id),
            created_at=data.get("created_at", now),
        )

    def get_stats(
        self,
        period: str = "24h",
        *,
        group_by: str = "model",
        org_id: str | None = None,
    ) -> StatsResponse:
        """Fetch summary stats for the given period (synchronous)."""
        params: dict[str, Any] = {"group_by": group_by}
        if org_id:
            params["org_id"] = org_id

        data = self._request("GET", "/api/v1/stats/summary", params=params)

        groups = [
            StatGroup(
                key=g["key"],
                request_count=g["request_count"],
                total_cost_usd=g["total_cost_usd"],
                avg_latency_ms=g["avg_latency_ms"],
                p95_latency_ms=g["p95_latency_ms"],
                avg_cost_per_request_usd=g["avg_cost_per_request_usd"],
                total_prompt_tokens=g["total_prompt_tokens"],
                total_completion_tokens=g["total_completion_tokens"],
                failure_count=g["failure_count"],
            )
            for g in data.get("groups", [])
        ]

        return StatsResponse(
            total_requests=data["total_requests"],
            total_cost_usd=data["total_cost_usd"],
            total_tokens=data["total_tokens"],
            failure_rate=data["failure_rate"],
            groups=groups,
        )

    def get_waste_score(
        self,
        *,
        org_id: str | None = None,
    ) -> WasteScoreResponse:
        """Fetch the current waste score (synchronous)."""
        params: dict[str, Any] = {}
        if org_id:
            params["org_id"] = org_id

        data = self._request("GET", "/api/v1/stats/waste-score", params=params)

        breakdown = [
            WasteBreakdownItem(
                task_type=item["task_type"],
                current_model=item["current_model"],
                suggested_model=item["suggested_model"],
                call_count=item["call_count"],
                current_cost_usd=item["current_cost_usd"],
                projected_cost_usd=item["projected_cost_usd"],
                savings_usd=item["savings_usd"],
                confidence=item["confidence"],
            )
            for item in data.get("breakdown", [])
        ]

        return WasteScoreResponse(
            waste_score=data["waste_score"],
            total_potential_savings_usd=data["total_potential_savings_usd"],
            breakdown=breakdown,
        )

    def get_fitness_matrix(
        self,
        *,
        org_id: str | None = None,
    ) -> FitnessMatrixResponse:
        """Fetch the fitness matrix from benchmark results (synchronous)."""
        params: dict[str, Any] = {}
        if org_id:
            params["org_id"] = org_id

        data = self._request(
            "GET", "/api/v1/benchmarks/fitness-matrix", params=params
        )

        entries = [
            FitnessEntry(
                model=e["model"],
                task_type=e["task_type"],
                quality_score=e["quality_score"],
                avg_cost=e["avg_cost"],
                avg_latency_ms=e["avg_latency_ms"],
                sample_count=e["sample_count"],
            )
            for e in data.get("entries", [])
        ]

        return FitnessMatrixResponse(entries=entries)
