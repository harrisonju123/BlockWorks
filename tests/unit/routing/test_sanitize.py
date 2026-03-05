"""Tests for routing.sanitize — provider-aware body sanitization."""

from __future__ import annotations

import copy

import pytest

from blockthrough.routing.sanitize import repair_tool_pairing, sanitize_for_target


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
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
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


class TestToolCallPairingRepair:
    """OpenAI-format tool_calls must have matching tool responses."""

    def test_removes_orphaned_tool_calls(self):
        """Assistant has tool_calls but no tool response — strip them."""
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "user", "content": "do something"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_abc", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                    ],
                },
                # No tool response for call_abc — conversation was interrupted
                {"role": "user", "content": "nevermind"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        assert "tool_calls" not in body["messages"][1]

    def test_keeps_paired_tool_calls(self):
        """Properly paired tool_calls are not touched."""
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "user", "content": "do something"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_abc", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_abc", "content": "file contents"},
                {"role": "assistant", "content": "Here's the file"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        assert len(body["messages"][1]["tool_calls"]) == 1

    def test_partial_orphan_keeps_paired_removes_orphaned(self):
        """Two tool_calls, one has a response, one doesn't."""
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                        {"id": "call_2", "type": "function", "function": {"name": "write", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
                # call_2 has no response
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        tcs = body["messages"][0]["tool_calls"]
        assert len(tcs) == 1
        assert tcs[0]["id"] == "call_1"


class TestAnthropicToolPairingRepair:
    """Anthropic-format tool_use blocks must have matching tool_result blocks."""

    def test_removes_orphaned_tool_use(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {"role": "user", "content": "do something"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check"},
                        {"type": "tool_use", "id": "tu_orphan", "name": "read", "input": {}},
                    ],
                },
                # No tool_result for tu_orphan
                {"role": "user", "content": "never mind"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        content = body["messages"][1]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_keeps_paired_tool_use(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                    ],
                },
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        content = body["messages"][0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "tool_use"

    def test_orphaned_tool_use_only_block_becomes_empty_string(self):
        body = {
            "model": "gpt-5.2-chat-latest",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_orphan", "name": "read", "input": {}},
                    ],
                },
                {"role": "user", "content": "skip"},
            ],
        }
        sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
        assert body["messages"][0]["content"] == ""


class TestRepairToolPairingStandalone:
    """repair_tool_pairing can be called independently of sanitize_for_target."""

    def test_fixes_orphaned_openai_format(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_orphan", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                    ],
                },
                {"role": "user", "content": "skip"},
            ],
        }
        repair_tool_pairing(body)
        assert "tool_calls" not in body["messages"][0]

    def test_fixes_orphaned_anthropic_format(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "let me check"},
                        {"type": "tool_use", "id": "toolu_orphan", "name": "read", "input": {}},
                    ],
                },
                {"role": "user", "content": "skip"},
            ],
        }
        repair_tool_pairing(body)
        content = body["messages"][0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_noop_when_all_paired(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_abc123", "name": "read", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "ok"},
                    ],
                },
            ],
        }
        repair_tool_pairing(body)
        assert len(body["messages"][0]["content"]) == 1
        assert body["messages"][0]["content"][0]["id"] == "toolu_abc123"

    def test_splits_mixed_tool_result_and_text_user_message(self):
        """Reproduces the LiteLLM translation bug: mixed tool_result + text blocks."""
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_abc", "name": "read", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_abc", "content": "file data"},
                        {"type": "text", "text": "now do something else"},
                        {"type": "text", "text": "also this"},
                    ],
                },
            ],
        }
        repair_tool_pairing(body)
        # Should be 3 messages now: assistant, user (tool_result only), user (text only)
        assert len(body["messages"]) == 3
        assert body["messages"][1]["role"] == "user"
        assert len(body["messages"][1]["content"]) == 1
        assert body["messages"][1]["content"][0]["type"] == "tool_result"
        assert body["messages"][2]["role"] == "user"
        assert all(b["type"] == "text" for b in body["messages"][2]["content"])

    def test_no_split_when_only_tool_results(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                    ],
                },
            ],
        }
        repair_tool_pairing(body)
        assert len(body["messages"]) == 1

    def test_no_split_when_only_text(self):
        body = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ],
        }
        repair_tool_pairing(body)
        assert len(body["messages"]) == 1

    def test_normalizes_call_prefix_to_toolu(self):
        """Round-tripped OpenAI IDs get normalized so LiteLLM translates correctly."""
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_CjRq5vBb", "name": "read", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_CjRq5vBb", "content": "ok"},
                    ],
                },
            ],
        }
        repair_tool_pairing(body)
        tu_id = body["messages"][0]["content"][0]["id"]
        tr_id = body["messages"][1]["content"][0]["tool_use_id"]
        assert tu_id.startswith("toolu_")
        assert tu_id == tr_id

    def test_leaves_toolu_prefix_alone(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_abc123", "name": "read", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "ok"},
                    ],
                },
            ],
        }
        repair_tool_pairing(body)
        assert body["messages"][0]["content"][0]["id"] == "toolu_abc123"
        assert body["messages"][1]["content"][0]["tool_use_id"] == "toolu_abc123"
