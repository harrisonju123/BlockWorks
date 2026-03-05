"""Unit tests for the LLM-based task classifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


class TestBuildPrompt:
    def test_basic_signals(self):
        inp = _make_input()
        prompt = _build_prompt(inp)
        assert "Signals:" in prompt
        assert "Code fences in system prompt: no" in prompt
        assert "JSON schema in output: no" in prompt

    def test_tools_present(self):
        inp = _make_input(has_tools=True, tool_count=5, has_tool_calls=True)
        prompt = _build_prompt(inp)
        assert "5 tools available" in prompt
        assert "model used tools" in prompt

    def test_tool_calls_without_tool_array(self):
        inp = _make_input(has_tools=False, has_tool_calls=True)
        prompt = _build_prompt(inp)
        assert "model used tools (no tool array)" in prompt

    def test_code_fences(self):
        inp = _make_input(has_code_fence_in_system=True)
        prompt = _build_prompt(inp)
        assert "Code fences in system prompt: yes" in prompt

    def test_json_schema(self):
        inp = _make_input(has_json_schema=True)
        prompt = _build_prompt(inp)
        assert "JSON schema in output: yes" in prompt

    def test_token_ratio_very_short(self):
        inp = _make_input(token_ratio=0.05)
        prompt = _build_prompt(inp)
        assert "very short output" in prompt

    def test_token_ratio_very_long(self):
        inp = _make_input(token_ratio=4.0)
        prompt = _build_prompt(inp)
        assert "very long output" in prompt

    def test_keywords_included(self):
        inp = _make_input(
            system_prompt_keywords=["implement", "function"],
            user_prompt_keywords=["write code", "refactor"],
        )
        prompt = _build_prompt(inp)
        assert "System keywords: implement, function" in prompt
        assert "User keywords: write code, refactor" in prompt

    def test_keywords_truncated_at_10(self):
        inp = _make_input(
            system_prompt_keywords=[f"kw{i}" for i in range(15)],
        )
        prompt = _build_prompt(inp)
        # Should only include first 10
        assert "kw9" in prompt
        assert "kw10" not in prompt

    def test_output_format_hint(self):
        inp = _make_input(output_format_hint="json")
        prompt = _build_prompt(inp)
        assert "Output format hint: json" in prompt

    def test_no_output_format_hint_omitted(self):
        inp = _make_input(output_format_hint=None)
        prompt = _build_prompt(inp)
        assert "Output format hint" not in prompt


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
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "code_generation"

        with patch("blockthrough.classifier.llm_classifier.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            inp = _make_input(has_code_fence_in_system=True)
            result = await llm_classify(inp, model="test-model")

            assert result.task_type == TaskType.CODE_GENERATION
            assert result.confidence == 0.85
            assert "llm_classifier:test-model" in result.signals

            # Verify the call was made with correct params
            call_kwargs = mock_litellm.acompletion.call_args.kwargs
            assert call_kwargs["model"] == "test-model"
            assert call_kwargs["max_tokens"] == 20
            assert call_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_invalid_response_returns_unknown(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "gibberish"

        with patch("blockthrough.classifier.llm_classifier.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            inp = _make_input()
            result = await llm_classify(inp)

            assert result.task_type == TaskType.UNKNOWN
            assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_api_error_propagates(self):
        """LLM errors should propagate so the caller can fall back to rules."""
        with patch("blockthrough.classifier.llm_classifier.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API down"))

            inp = _make_input()
            with pytest.raises(RuntimeError, match="API down"):
                await llm_classify(inp)

    @pytest.mark.asyncio
    async def test_timeout_propagates(self):
        """Timeout should propagate so the caller can fall back."""
        import asyncio

        async def slow_completion(**kwargs):
            await asyncio.sleep(10)

        with patch("blockthrough.classifier.llm_classifier.litellm") as mock_litellm:
            mock_litellm.acompletion = slow_completion

            inp = _make_input()
            with pytest.raises(asyncio.TimeoutError):
                await llm_classify(inp, timeout_s=0.01)
