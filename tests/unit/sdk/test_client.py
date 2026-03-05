"""Tests for the BlockThrough Python SDK client.

All HTTP calls are mocked — no real API server needed. Tests cover
the async SDK, sync wrapper, error handling, and response parsing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from blockthrough.sdk.client import BlockThroughClient, BlockThroughError, BlockThroughSDK


class TestBlockThroughSDKTrack:
    """Async SDK: track() sends correct payload and parses response."""

    @pytest.mark.asyncio
    async def test_track_event_success(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"event_id": "abc-123", "created_at": "2026-03-03T00:00:00Z", "status": "accepted"},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                result = await sdk.track(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hello"}],
                    completion="world",
                    prompt_tokens=10,
                    completion_tokens=5,
                    estimated_cost=0.001,
                    latency_ms=150.0,
                )

                assert result.event_id == "abc-123"
                assert result.created_at == "2026-03-03T00:00:00Z"

    @pytest.mark.asyncio
    async def test_track_event_with_metadata(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"event_id": "def-456", "created_at": "2026-03-03T00:00:00Z", "status": "accepted"},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                await sdk.track(
                    model="claude-sonnet-4-20250514",
                    messages=[{"role": "user", "content": "test"}],
                    completion="response",
                    prompt_tokens=100,
                    completion_tokens=50,
                    estimated_cost=0.01,
                    latency_ms=200.0,
                    session_id="sess-1",
                    trace_id="trace-1",
                    org_id="org-1",
                    user_id="user-1",
                    metadata={"env": "test"},
                )

                # Verify the request was made with correct payload
                call_args = mock_req.call_args
                payload = call_args.kwargs.get("json") or call_args[1].get("json")
                assert payload["model"] == "claude-sonnet-4-20250514"
                assert payload["session_id"] == "sess-1"
                assert payload["trace_id"] == "trace-1"
                assert payload["org_id"] == "org-1"
                assert payload["custom_metadata"] == {"env": "test"}

    @pytest.mark.asyncio
    async def test_track_includes_content_hashes(self) -> None:
        """Payload should include prompt_hash and completion_hash, not raw content."""
        mock_response = httpx.Response(
            200,
            json={"event_id": "x", "created_at": "2026-03-03T00:00:00Z", "status": "accepted"},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                await sdk.track(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "sensitive data"}],
                    completion="private response",
                    prompt_tokens=10,
                    completion_tokens=5,
                    estimated_cost=0.001,
                    latency_ms=100.0,
                )

                payload = mock_req.call_args.kwargs.get("json") or mock_req.call_args[1].get("json")
                # Hashes should be hex strings, not the raw content
                assert "sensitive data" not in json.dumps(payload)
                assert "private response" not in json.dumps(payload)
                assert len(payload["prompt_hash"]) == 64  # SHA-256 hex
                assert len(payload["completion_hash"]) == 64


class TestBlockThroughSDKGetStats:
    """Async SDK: get_stats() correctly parses the summary response."""

    @pytest.mark.asyncio
    async def test_get_stats_success(self) -> None:
        mock_data = {
            "period": {"start": "2026-03-02T00:00:00Z", "end": "2026-03-03T00:00:00Z"},
            "total_requests": 1500,
            "total_cost_usd": 12.50,
            "total_tokens": 500000,
            "failure_rate": 0.02,
            "groups": [
                {
                    "key": "gpt-4o",
                    "request_count": 1000,
                    "total_cost_usd": 10.0,
                    "avg_latency_ms": 200.0,
                    "p95_latency_ms": 500.0,
                    "avg_cost_per_request_usd": 0.01,
                    "total_prompt_tokens": 300000,
                    "total_completion_tokens": 100000,
                    "failure_count": 20,
                },
            ],
        }
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                stats = await sdk.get_stats(period="24h")

                assert stats.total_requests == 1500
                assert stats.total_cost_usd == 12.50
                assert len(stats.groups) == 1
                assert stats.groups[0].key == "gpt-4o"
                assert stats.groups[0].request_count == 1000


class TestBlockThroughSDKGetWasteScore:

    @pytest.mark.asyncio
    async def test_get_waste_score_success(self) -> None:
        mock_data = {
            "waste_score": 0.35,
            "total_potential_savings_usd": 42.0,
            "breakdown": [
                {
                    "task_type": "classification",
                    "current_model": "claude-opus-4-20250514",
                    "suggested_model": "gpt-4o-mini",
                    "call_count": 500,
                    "current_cost_usd": 50.0,
                    "projected_cost_usd": 8.0,
                    "savings_usd": 42.0,
                    "confidence": 0.9,
                },
            ],
        }
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                waste = await sdk.get_waste_score()

                assert waste.waste_score == 0.35
                assert waste.total_potential_savings_usd == 42.0
                assert len(waste.breakdown) == 1
                assert waste.breakdown[0].task_type == "classification"


class TestBlockThroughSDKGetFitnessMatrix:

    @pytest.mark.asyncio
    async def test_get_fitness_matrix_success(self) -> None:
        mock_data = {
            "entries": [
                {
                    "model": "gpt-4o",
                    "task_type": "code_generation",
                    "quality_score": 0.85,
                    "avg_cost": 0.012,
                    "avg_latency_ms": 1500.0,
                    "sample_count": 200,
                },
            ],
        }
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                matrix = await sdk.get_fitness_matrix()

                assert len(matrix.entries) == 1
                assert matrix.entries[0].model == "gpt-4o"
                assert matrix.entries[0].quality_score == 0.85


class TestBlockThroughSDKErrorHandling:

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        mock_response = httpx.Response(
            500,
            json={"detail": "Internal server error"},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                with pytest.raises(BlockThroughError) as exc_info:
                    await sdk.get_stats()

                assert exc_info.value.status_code == 500
                assert "Internal server error" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_422_error_detail(self) -> None:
        mock_response = httpx.Response(
            422,
            json={"detail": "model is required"},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            async with BlockThroughSDK(api_url="http://test:8100") as sdk:
                with pytest.raises(BlockThroughError) as exc_info:
                    await sdk.track(
                        model="",
                        messages=[],
                        completion="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        estimated_cost=0,
                        latency_ms=0,
                    )

                assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_auth_header_set_when_api_key_provided(self) -> None:
        mock_response = httpx.Response(200, json={"total_requests": 0, "total_cost_usd": 0, "total_tokens": 0, "failure_rate": 0, "groups": []})

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_response):
            sdk = BlockThroughSDK(api_url="http://test:8100", api_key="secret-key")
            client = await sdk._ensure_client()

            assert client.headers["Authorization"] == "Bearer secret-key"
            await sdk.close()


class TestBlockThroughSyncClient:
    """Sync wrapper: verifies it delegates to httpx.Client correctly."""

    def test_track_event_sync(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"event_id": "sync-123", "created_at": "2026-03-03T00:00:00Z", "status": "accepted"},
        )

        with patch("httpx.Client.request", return_value=mock_response):
            with BlockThroughClient(api_url="http://test:8100") as client:
                result = client.track(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "hi"}],
                    completion="hello",
                    prompt_tokens=5,
                    completion_tokens=3,
                    estimated_cost=0.0001,
                    latency_ms=50.0,
                )

                assert result.event_id == "sync-123"

    def test_get_stats_sync(self) -> None:
        mock_data = {
            "total_requests": 100,
            "total_cost_usd": 1.0,
            "total_tokens": 10000,
            "failure_rate": 0.0,
            "groups": [],
        }
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.Client.request", return_value=mock_response):
            with BlockThroughClient(api_url="http://test:8100") as client:
                stats = client.get_stats()
                assert stats.total_requests == 100
                assert stats.groups == []

    def test_get_waste_score_sync(self) -> None:
        mock_data = {
            "waste_score": 0.0,
            "total_potential_savings_usd": 0.0,
            "breakdown": [],
        }
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.Client.request", return_value=mock_response):
            with BlockThroughClient(api_url="http://test:8100") as client:
                waste = client.get_waste_score()
                assert waste.waste_score == 0.0

    def test_get_fitness_matrix_sync(self) -> None:
        mock_data = {"entries": []}
        mock_response = httpx.Response(200, json=mock_data)

        with patch("httpx.Client.request", return_value=mock_response):
            with BlockThroughClient(api_url="http://test:8100") as client:
                matrix = client.get_fitness_matrix()
                assert matrix.entries == []

    def test_sync_error_handling(self) -> None:
        mock_response = httpx.Response(
            404,
            json={"detail": "Not found"},
        )

        with patch("httpx.Client.request", return_value=mock_response):
            with BlockThroughClient(api_url="http://test:8100") as client:
                with pytest.raises(BlockThroughError) as exc_info:
                    client.get_stats()

                assert exc_info.value.status_code == 404

    def test_context_manager_closes_client(self) -> None:
        with BlockThroughClient(api_url="http://test:8100") as client:
            client._ensure_client()
            assert client._sync_client is not None
        # After exit, client should be closed
        assert client._sync_client is None
