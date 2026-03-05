"""Tests for SDK decorators and context managers.

Validates that @track_llm_call correctly wraps functions, that
blockthrough_trace groups calls under a shared trace, and that
the provider monkey-patching works as expected.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call

from blockthrough.sdk.decorators import (
    _get_active_trace,
    agentproof_trace,
    blockthrough_trace,
    track_anthropic,
    track_llm_call,
    track_openai,
)


class TestTrackLLMCall:

    def test_decorator_preserves_return_value(self) -> None:
        """The decorator should not alter what the wrapped function returns."""
        @track_llm_call(model="gpt-4o")
        def my_func() -> dict:
            return {
                "completion": "hello",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost": 0.001,
            }

        result = my_func()
        assert result["completion"] == "hello"

    def test_decorator_calls_client_track(self) -> None:
        """When a client is provided, the decorator should call client.track()."""
        mock_client = MagicMock()

        @track_llm_call(client=mock_client, model="gpt-4o", provider="openai")
        def my_func() -> dict:
            return {
                "completion": "response",
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "cost": 0.005,
            }

        my_func()

        mock_client.track.assert_called_once()
        call_kwargs = mock_client.track.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["provider"] == "openai"
        assert call_kwargs["prompt_tokens"] == 50
        assert call_kwargs["completion_tokens"] == 20

    def test_decorator_handles_exception_in_extraction(self) -> None:
        """If the result doesn't have expected fields, decorator should not crash."""
        @track_llm_call(model="gpt-4o")
        def my_func() -> str:
            return "not a dict"

        # Should not raise
        result = my_func()
        assert result == "not a dict"

    def test_decorator_with_object_result(self) -> None:
        """Decorator should handle object-style results (hasattr __dict__)."""
        mock_client = MagicMock()

        class LLMResult:
            def __init__(self):
                self.completion = "test"
                self.prompt_tokens = 10
                self.completion_tokens = 5
                self.cost = 0.001
                self.model = "custom-model"

        @track_llm_call(client=mock_client, model="fallback")
        def my_func() -> LLMResult:
            return LLMResult()

        result = my_func()
        assert result.completion == "test"
        mock_client.track.assert_called_once()
        # Should use the model from the result, not the hint
        assert mock_client.track.call_args.kwargs["model"] == "custom-model"


class TestBlockthroughTrace:

    def test_trace_context_active_inside_block(self) -> None:
        """Inside the context manager, _get_active_trace() should return the context."""
        with blockthrough_trace("session-1") as trace:
            active = _get_active_trace()
            assert active is not None
            assert active.session_id == "session-1"
            assert active.trace_id == trace.trace_id

    def test_trace_context_none_outside_block(self) -> None:
        """After exiting, the context var should be reset to None."""
        with blockthrough_trace("session-1"):
            pass

        assert _get_active_trace() is None

    def test_trace_uses_custom_trace_id(self) -> None:
        with blockthrough_trace("session-1", trace_id="custom-trace") as trace:
            assert trace.trace_id == "custom-trace"

    def test_trace_auto_generates_trace_id(self) -> None:
        with blockthrough_trace("session-1") as trace:
            assert len(trace.trace_id) > 0  # UUID string

    def test_events_list_starts_empty(self) -> None:
        with blockthrough_trace("session-1") as trace:
            assert trace.events == []

    def test_nested_calls_share_trace(self) -> None:
        """Functions decorated with @track_llm_call inside a trace block
        should see the same trace context."""
        mock_client = MagicMock()
        captured_trace_ids: list[str | None] = []

        @track_llm_call(client=mock_client, model="gpt-4o")
        def call_1() -> dict:
            ctx = _get_active_trace()
            captured_trace_ids.append(ctx.trace_id if ctx else None)
            return {"completion": "a", "prompt_tokens": 1, "completion_tokens": 1, "cost": 0}

        @track_llm_call(client=mock_client, model="gpt-4o")
        def call_2() -> dict:
            ctx = _get_active_trace()
            captured_trace_ids.append(ctx.trace_id if ctx else None)
            return {"completion": "b", "prompt_tokens": 1, "completion_tokens": 1, "cost": 0}

        with blockthrough_trace("session-1") as trace:
            call_1()
            call_2()

        # Both calls should have seen the same trace_id
        assert len(captured_trace_ids) == 2
        assert captured_trace_ids[0] == trace.trace_id
        assert captured_trace_ids[1] == trace.trace_id


class TestTrackOpenAI:

    def test_patches_create_method(self) -> None:
        """track_openai should replace chat.completions.create."""
        original_create = MagicMock(name="original_create")

        mock_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=original_create)
            )
        )

        track_openai(mock_client)

        # The create method should now be wrapped
        assert mock_client.chat.completions.create is not original_create

    def test_patched_create_calls_original(self) -> None:
        """The patched create should still call the original implementation."""
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        message = SimpleNamespace(content="hello")
        choice = SimpleNamespace(message=message)
        mock_result = SimpleNamespace(
            choices=[choice], usage=usage, model="gpt-4o"
        )

        original_create = MagicMock(return_value=mock_result)
        mock_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=original_create)
            )
        )

        track_openai(mock_client)
        result = mock_client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

        original_create.assert_called_once()
        assert result is mock_result

    def test_patched_create_reports_to_blockthrough(self) -> None:
        """When an blockthrough_client is provided, track() should be called."""
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        message = SimpleNamespace(content="hello")
        choice = SimpleNamespace(message=message)
        mock_result = SimpleNamespace(
            choices=[choice], usage=usage, model="gpt-4o"
        )

        original_create = MagicMock(return_value=mock_result)
        mock_openai = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=original_create)
            )
        )
        mock_ap = MagicMock()

        track_openai(mock_openai, blockthrough_client=mock_ap)
        mock_openai.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

        mock_ap.track.assert_called_once()
        assert mock_ap.track.call_args.kwargs["model"] == "gpt-4o"
        assert mock_ap.track.call_args.kwargs["provider"] == "openai"

    def test_skips_when_no_chat_attribute(self) -> None:
        """If the client doesn't have .chat, should return it unmodified."""
        mock_client = SimpleNamespace(other="stuff")
        result = track_openai(mock_client)
        assert result is mock_client


class TestTrackAnthropic:

    def test_patches_messages_create(self) -> None:
        original_create = MagicMock(name="original_create")
        mock_client = SimpleNamespace(
            messages=SimpleNamespace(create=original_create)
        )

        track_anthropic(mock_client)
        assert mock_client.messages.create is not original_create

    def test_patched_create_calls_original(self) -> None:
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        content_block = SimpleNamespace(text="response")
        mock_result = SimpleNamespace(
            content=[content_block], usage=usage, model="claude-sonnet-4-20250514"
        )

        original_create = MagicMock(return_value=mock_result)
        mock_client = SimpleNamespace(
            messages=SimpleNamespace(create=original_create)
        )

        track_anthropic(mock_client)
        result = mock_client.messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "hi"}],
        )

        original_create.assert_called_once()
        assert result is mock_result

    def test_patched_create_reports_to_blockthrough(self) -> None:
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        content_block = SimpleNamespace(text="response")
        mock_result = SimpleNamespace(
            content=[content_block], usage=usage, model="claude-sonnet-4-20250514"
        )

        original_create = MagicMock(return_value=mock_result)
        mock_anthropic = SimpleNamespace(
            messages=SimpleNamespace(create=original_create)
        )
        mock_ap = MagicMock()

        track_anthropic(mock_anthropic, blockthrough_client=mock_ap)
        mock_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "hi"}],
        )

        mock_ap.track.assert_called_once()
        assert mock_ap.track.call_args.kwargs["model"] == "claude-sonnet-4-20250514"
        assert mock_ap.track.call_args.kwargs["provider"] == "anthropic"

    def test_skips_when_no_messages_attribute(self) -> None:
        mock_client = SimpleNamespace(other="stuff")
        result = track_anthropic(mock_client)
        assert result is mock_client
