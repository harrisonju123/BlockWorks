"""Tests for the Anthropic → OpenAI message format converter."""

from __future__ import annotations

from blockthrough.benchmarking.convert import (
    convert_anthropic_to_openai,
    is_anthropic_format,
)


class TestIsAnthropicFormat:

    def test_plain_string_content(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        assert is_anthropic_format(msgs) is False

    def test_content_block_array(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        assert is_anthropic_format(msgs) is True

    def test_tool_result_block(self) -> None:
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "ok"},
        ]}]
        assert is_anthropic_format(msgs) is True

    def test_empty_messages(self) -> None:
        assert is_anthropic_format([]) is False

    def test_no_content(self) -> None:
        msgs = [{"role": "user"}]
        assert is_anthropic_format(msgs) is False

    def test_list_without_type_key(self) -> None:
        """A list of plain strings shouldn't trigger detection."""
        msgs = [{"role": "user", "content": ["a", "b"]}]
        assert is_anthropic_format(msgs) is False


class TestConvertAnthropicToOpenai:

    def test_system_prompt_string(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = convert_anthropic_to_openai(msgs, system="You are a helper")
        assert result[0] == {"role": "system", "content": "You are a helper"}
        assert result[1] == {"role": "user", "content": "hi"}

    def test_system_prompt_content_blocks(self) -> None:
        system = [
            {"type": "text", "text": "You are"},
            {"type": "text", "text": "helpful"},
        ]
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = convert_anthropic_to_openai(msgs, system=system)
        assert result[0] == {"role": "system", "content": "You are helpful"}

    def test_system_prompt_none(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = convert_anthropic_to_openai(msgs, system=None)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_system_prompt_empty_string(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = convert_anthropic_to_openai(msgs, system="")
        assert result[0]["role"] == "user"

    def test_text_content_blocks_to_string(self) -> None:
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert result[0] == {"role": "user", "content": "hello world"}

    def test_tool_use_to_tool_calls(self) -> None:
        msgs = [{"role": "assistant", "content": [
            {"type": "tool_use", "id": "tc_1", "name": "get_weather", "input": {"city": "NYC"}},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tc_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"city": "NYC"}'

    def test_tool_result_to_tool_message(self) -> None:
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc_1", "content": "72°F"},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 1
        assert result[0] == {
            "role": "tool",
            "tool_call_id": "tc_1",
            "content": "72°F",
        }

    def test_tool_result_with_nested_content_blocks(self) -> None:
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc_1", "content": [
                {"type": "text", "text": "result text"},
            ]},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert result[0]["content"] == "result text"

    def test_mixed_user_message_text_and_tool_result(self) -> None:
        """User message with both text and tool_result blocks should split."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "Here are the results:"},
            {"type": "tool_result", "tool_use_id": "tc_1", "content": "done"},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Here are the results:"}
        assert result[1] == {"role": "tool", "tool_call_id": "tc_1", "content": "done"}

    def test_thinking_blocks_stripped(self) -> None:
        msgs = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think..."},
            {"type": "text", "text": "The answer is 42"},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "The answer is 42"

    def test_image_blocks_stripped(self) -> None:
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "..."}},
            {"type": "text", "text": "What is this?"},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "What is this?"}

    def test_plain_string_passthrough(self) -> None:
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = convert_anthropic_to_openai(msgs)
        assert result == msgs

    def test_full_multi_turn_conversation(self) -> None:
        """End-to-end: system + user + assistant(tool_use) + user(tool_result) + assistant(text)."""
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "What's the weather?"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc_1", "name": "get_weather", "input": {"city": "NYC"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc_1", "content": "72°F and sunny"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "It's 72°F and sunny in NYC!"},
            ]},
        ]
        result = convert_anthropic_to_openai(msgs, system="You are a weather bot")

        assert len(result) == 5
        assert result[0] == {"role": "system", "content": "You are a weather bot"}
        assert result[1] == {"role": "user", "content": "What's the weather?"}
        assert result[2]["role"] == "assistant"
        assert result[2]["tool_calls"][0]["function"]["name"] == "get_weather"
        assert result[3] == {"role": "tool", "tool_call_id": "tc_1", "content": "72°F and sunny"}
        assert result[4] == {"role": "assistant", "content": "It's 72°F and sunny in NYC!"}

    def test_assistant_with_text_and_tool_use(self) -> None:
        """Assistant message with both text and tool_use blocks."""
        msgs = [{"role": "assistant", "content": [
            {"type": "text", "text": "I'll check that for you."},
            {"type": "tool_use", "id": "tc_1", "name": "search", "input": {"q": "test"}},
        ]}]
        result = convert_anthropic_to_openai(msgs)
        assert len(result) == 1
        msg = result[0]
        assert msg["content"] == "I'll check that for you."
        assert len(msg["tool_calls"]) == 1
