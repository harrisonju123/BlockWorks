"""Tests for routing.sanitize — provider-aware body sanitization."""

from __future__ import annotations

import copy

import pytest

from blockthrough.routing.sanitize import sanitize_for_target


class TestSanitizeForTarget:
    """sanitize_for_target strips provider-specific params on cross-provider routing."""

    def test_noop_same_provider(self):
        body = {
            "model": "claude-opus-4-6",
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "output_config": {"format": "json"},
            "messages": [{"role": "user", "content": "hello"}],
        }
        original = copy.deepcopy(body)
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="claude-sonnet-4-6")
        assert body == original

    def test_strips_anthropic_params_for_openai_target(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "output_config": {"format": "json"},
            "top_k": 40,
            "metadata": {"user_id": "abc"},
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")

        assert "thinking" not in body
        assert "output_config" not in body
        assert "top_k" not in body
        assert "metadata" not in body
        # Core params preserved
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 1024
        assert body["messages"] == [{"role": "user", "content": "hello"}]

    def test_strips_thinking_content_blocks(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "user", "content": "explain X"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "let me reason..."},
                        {"type": "text", "text": "Here is my answer"},
                    ],
                },
                {"role": "user", "content": "thanks"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")

        assistant_content = body["messages"][1]["content"]
        assert len(assistant_content) == 1
        assert assistant_content[0]["type"] == "text"

    def test_strips_anthropic_params_for_google_target(self):
        body = {
            "model": "google.gemma-3-27b-it",
            "output_config": {"format": "json"},
            "thinking": {"type": "enabled"},
            "messages": [{"role": "user", "content": "hi"}],
        }
        sanitize_for_target(body, source_model="claude-sonnet-4-6", target_model="google.gemma-3-27b-it")
        assert "output_config" not in body
        assert "thinking" not in body

    def test_strips_anthropic_params_for_mistral_target(self):
        body = {
            "model": "mistral.ministral-3-14b-instruct",
            "top_k": 50,
            "metadata": {"user_id": "test"},
            "messages": [],
        }
        sanitize_for_target(body, source_model="claude-haiku-4-5-20251001", target_model="mistral.ministral-3-14b-instruct")
        assert "top_k" not in body
        assert "metadata" not in body

    def test_strips_openai_params_for_anthropic_target(self):
        body = {
            "model": "claude-sonnet-4-6",
            "logprobs": True,
            "top_logprobs": 5,
            "logit_bias": {"123": 1.0},
            "parallel_tool_calls": True,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.5,
        }
        sanitize_for_target(body, source_model="gpt-5.2-chat-latest", target_model="claude-sonnet-4-6")
        assert "logprobs" not in body
        assert "top_logprobs" not in body
        assert "logit_bias" not in body
        assert "parallel_tool_calls" not in body
        assert body["temperature"] == 0.5

    def test_noop_for_unknown_source_provider(self):
        body = {
            "model": "claude-sonnet-4-6",
            "custom_param": "value",
            "messages": [],
        }
        original = copy.deepcopy(body)
        sanitize_for_target(body, source_model="some-unknown-model", target_model="claude-sonnet-4-6")
        assert body == original

    def test_preserves_non_thinking_assistant_blocks(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hmm..."},
                        {"type": "text", "text": "answer"},
                        {"type": "tool_use", "id": "t1", "name": "read", "input": {}},
                    ],
                },
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        content = body["messages"][0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"

    def test_skips_non_assistant_messages(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ],
        }
        original_messages = copy.deepcopy(body["messages"])
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        assert body["messages"] == original_messages

    def test_handles_string_content_in_assistant_messages(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "assistant", "content": "just a string"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        assert body["messages"][0]["content"] == "just a string"
