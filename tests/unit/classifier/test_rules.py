"""Tests for the rules-based classifier."""

from agentproof.classifier.rules import classify, extract_keywords
from agentproof.classifier.taxonomy import ClassifierInput
from agentproof.types import TaskType


def _make_input(**overrides) -> ClassifierInput:
    defaults = {
        "system_prompt_hash": "abc",
        "has_tools": False,
        "tool_count": 0,
        "has_json_schema": False,
        "has_code_fence_in_system": False,
        "prompt_token_count": 500,
        "completion_token_count": 200,
        "token_ratio": 0.4,
        "model": "claude-sonnet-4-20250514",
        "system_prompt_keywords": [],
        "output_format_hint": None,
    }
    defaults.update(overrides)
    return ClassifierInput(**defaults)


class TestRulesClassifier:
    def test_tool_selection(self):
        inp = _make_input(has_tools=True, tool_count=5)
        result = classify(inp)
        assert result.task_type == TaskType.TOOL_SELECTION
        assert "tool_array_present" in result.signals

    def test_code_generation_keywords(self):
        inp = _make_input(
            system_prompt_keywords=["implement", "function"],
            has_code_fence_in_system=True,
        )
        result = classify(inp)
        assert result.task_type == TaskType.CODE_GENERATION

    def test_classification_low_output(self):
        inp = _make_input(
            system_prompt_keywords=["classify"],
            has_json_schema=True,
            token_ratio=0.05,
            completion_token_count=20,
        )
        result = classify(inp)
        assert result.task_type == TaskType.CLASSIFICATION

    def test_summarization_keywords(self):
        inp = _make_input(system_prompt_keywords=["summarize", "brief"])
        result = classify(inp)
        assert result.task_type == TaskType.SUMMARIZATION

    def test_conversation_fallback(self):
        """With no strong signals, classifier falls back to UNKNOWN at low confidence."""
        inp = _make_input()
        result = classify(inp)
        assert result.task_type in (TaskType.CONVERSATION, TaskType.UNKNOWN)
        assert "no_strong_signals_conversation_fallback" in result.signals

    def test_confidence_range(self):
        inp = _make_input(has_tools=True, tool_count=10)
        result = classify(inp)
        assert 0.0 <= result.confidence <= 1.0

    def test_signals_populated(self):
        inp = _make_input(has_tools=True, has_json_schema=True)
        result = classify(inp)
        assert len(result.signals) > 0


class TestExtractKeywordsWordBoundary:
    """Verify that single-word keywords use word-boundary matching
    to avoid substring false positives (e.g. "class" in "classify")."""

    def test_class_does_not_match_classify(self):
        result = extract_keywords("Please classify this document")
        assert "classify" in result
        assert "class" not in result

    def test_help_does_not_match_helpful(self):
        result = extract_keywords("This is a helpful assistant")
        assert "help" not in result

    def test_multiword_keywords_still_match(self):
        result = extract_keywords("please write code for me")
        assert "write code" in result

    def test_exact_single_word_still_matches(self):
        """Exact word occurrence must still be detected."""
        result = extract_keywords("define a class for the model")
        assert "class" in result

    def test_help_exact_word_matches(self):
        result = extract_keywords("I need help with this task")
        assert "help" in result
