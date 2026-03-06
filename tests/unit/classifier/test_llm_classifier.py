"""Unit tests for the LLM-based task classifier."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from blockthrough.classifier.llm_classifier import (
    _build_prompt,
    _parse_response,
    llm_classify,
)
from blockthrough.classifier.taxonomy import ClassifierInput
from blockthrough.types import TaskType


def _make_input(**overrides) -> ClassifierInput:
    defaults = dict(
        system_prompt_hash="abc123",
        has_tools=False,
        tool_count=0,
        has_json_schema=False,
        has_code_fence_in_system=False,
        prompt_token_count=100,
        completion_token_count=200,
        token_ratio=2.0,
        model="test-model",
        system_prompt_keywords=[],
        user_prompt_keywords=[],
        has_tool_calls=False,
        output_format_hint=None,
    )
    defaults.update(overrides)
    return ClassifierInput(**defaults)


def _mock_httpx_response(content: str, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response matching OpenAI chat completions format."""
    body = {
        "choices": [{"message": {"content": content}}],
    }
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )


class TestBuildPrompt:
    def test_user_message_used_as_prompt(self):
        inp = _make_input(last_user_message="Write a Python function to sort a list")
        prompt = _build_prompt(inp)
        assert "User message:" in prompt
        assert "Write a Python function to sort a list" in prompt

    def test_user_message_truncated(self):
        inp = _make_input(last_user_message="x" * 600)
        prompt = _build_prompt(inp)
        assert len(prompt.split("User message:\n")[1]) == 500

    def test_no_user_message_fallback(self):
        inp = _make_input(last_user_message=None)
        prompt = _build_prompt(inp)
        assert "No user message available" in prompt


class TestParseResponse:
    @pytest.mark.parametrize(
        "raw,expected_type",
        [
            ("code_generation", TaskType.CODE_GENERATION),
            ("CODE_GENERATION", TaskType.CODE_GENERATION),
            ("  code_review  ", TaskType.CODE_REVIEW),
            ("classification", TaskType.CLASSIFICATION),
            ("summarization", TaskType.SUMMARIZATION),
            ("extraction", TaskType.EXTRACTION),
            ("reasoning", TaskType.REASONING),
            ("conversation", TaskType.CONVERSATION),
            ("tool_selection", TaskType.TOOL_SELECTION),
        ],
    )
    def test_valid_types(self, raw, expected_type):
        task_type, confidence = _parse_response(raw)
        assert task_type == expected_type
        assert confidence == 0.85

    def test_hyphenated_form(self):
        task_type, confidence = _parse_response("code-generation")
        assert task_type == TaskType.CODE_GENERATION

    def test_prefix_stripped(self):
        task_type, _ = _parse_response("category: code_review")
        assert task_type == TaskType.CODE_REVIEW

    def test_type_prefix_stripped(self):
        task_type, _ = _parse_response("type: extraction")
        assert task_type == TaskType.EXTRACTION

    def test_fuzzy_match_in_longer_string(self):
        task_type, confidence = _parse_response("I think this is code_generation because...")
        assert task_type == TaskType.CODE_GENERATION
        assert confidence == 0.7  # lower confidence for fuzzy match

    def test_invalid_response(self):
        task_type, confidence = _parse_response("I don't know")
        assert task_type == TaskType.UNKNOWN
        assert confidence == 0.0

    def test_empty_response(self):
        task_type, confidence = _parse_response("")
        assert task_type == TaskType.UNKNOWN
        assert confidence == 0.0


class TestLlmClassify:
    @pytest.mark.asyncio
    async def test_successful_classification(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_httpx_response("code_generation")

        inp = _make_input(has_code_fence_in_system=True)
        result = await llm_classify(inp, model="test-model", client=mock_client)

        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 0.85
        assert "llm_classifier:test-model" in result.signals

        # Verify the POST was made to the right endpoint with correct body
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/v1/chat/completions"
        body = call_args[1]["json"]
        assert body["model"] == "test-model"
        assert body["max_tokens"] == 20
        assert body["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_invalid_response_returns_unknown(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_httpx_response("gibberish")

        inp = _make_input()
        result = await llm_classify(inp, client=mock_client)

        assert result.task_type == TaskType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        """HTTP errors should propagate so the caller can fall back to rules."""
        error_resp = _mock_httpx_response("", status_code=500)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = error_resp

        inp = _make_input()
        with pytest.raises(httpx.HTTPStatusError):
            await llm_classify(inp, client=mock_client)

    @pytest.mark.asyncio
    async def test_api_key_sent_as_bearer(self):
        """When api_key is provided, it should be sent as Authorization header."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_httpx_response("code_generation")

        inp = _make_input()
        await llm_classify(inp, client=mock_client, api_key="sk-test-key")

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-key"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_no_key(self):
        """When api_key is None, no Authorization header should be sent."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_httpx_response("code_generation")

        inp = _make_input()
        await llm_classify(inp, client=mock_client)

        call_kwargs = mock_client.post.call_args[1]
        assert "Authorization" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        """Calling without a client should raise RuntimeError."""
        inp = _make_input()
        with pytest.raises(RuntimeError, match="requires an httpx client"):
            await llm_classify(inp)

    @pytest.mark.asyncio
    async def test_timeout_propagates(self):
        """Timeout should propagate so the caller can fall back."""
        async def slow_post(*args, **kwargs):
            await asyncio.sleep(10)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = slow_post

        inp = _make_input()
        with pytest.raises(asyncio.TimeoutError):
            await llm_classify(inp, timeout_s=0.01, client=mock_client)

    @pytest.mark.asyncio
    async def test_empty_choices_raises_valueerror(self):
        """H3: Empty choices array → ValueError so caller falls back to rules."""
        resp = httpx.Response(
            status_code=200,
            json={"choices": []},
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp

        inp = _make_input()
        with pytest.raises(ValueError, match="no choices"):
            await llm_classify(inp, client=mock_client)

    @pytest.mark.asyncio
    async def test_missing_message_key_raises_valueerror(self):
        """H3: choices[0] without 'message' → ValueError."""
        resp = httpx.Response(
            status_code=200,
            json={"choices": [{}]},
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp

        inp = _make_input()
        with pytest.raises(ValueError, match="no message content"):
            await llm_classify(inp, client=mock_client)

    @pytest.mark.asyncio
    async def test_null_content_raises_valueerror(self):
        """H3: message.content is None → ValueError."""
        resp = httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": None}}]},
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp

        inp = _make_input()
        with pytest.raises(ValueError, match="no message content"):
            await llm_classify(inp, client=mock_client)
