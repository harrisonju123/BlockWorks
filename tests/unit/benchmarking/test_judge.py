"""Tests for the LLM-as-judge evaluation engine.

All tests mock litellm.acompletion so no real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blockthrough.benchmarking.judge import (
    RUBRIC_VERSION,
    _build_judge_prompt,
    _parse_judge_response,
    compute_weighted_score,
    evaluate,
    get_rubric,
)
from blockthrough.benchmarking.types import Rubric, RubricCriterion
from blockthrough.types import TaskType


class TestGetRubric:

    def test_rubric_exists_for_all_non_unknown_task_types(self) -> None:
        for tt in TaskType:
            if tt == TaskType.UNKNOWN:
                assert get_rubric(tt) is None
            else:
                rubric = get_rubric(tt)
                assert rubric is not None, f"Missing rubric for {tt.value}"
                assert rubric.task_type == tt

    def test_rubric_weights_sum_to_one(self) -> None:
        for tt in TaskType:
            rubric = get_rubric(tt)
            if rubric is None:
                continue
            total_weight = sum(c.weight for c in rubric.criteria)
            assert total_weight == pytest.approx(1.0), (
                f"Rubric weights for {tt.value} sum to {total_weight}, expected 1.0"
            )

    def test_unknown_returns_none(self) -> None:
        assert get_rubric(TaskType.UNKNOWN) is None


class TestBuildJudgePrompt:

    def test_prompt_contains_all_sections(self) -> None:
        rubric = get_rubric(TaskType.CODE_GENERATION)
        assert rubric is not None
        prompt = _build_judge_prompt(
            original_prompt="Write a sort function",
            original_completion="def sort(x): return sorted(x)",
            benchmark_completion="def sort(lst): return sorted(lst)",
            rubric=rubric,
        )
        assert "ORIGINAL PROMPT:" in prompt
        assert "REFERENCE COMPLETION" in prompt
        assert "BENCHMARK COMPLETION" in prompt
        assert "RUBRIC:" in prompt
        assert "code_generation" in prompt

    def test_prompt_includes_all_criteria(self) -> None:
        rubric = get_rubric(TaskType.CODE_GENERATION)
        assert rubric is not None
        prompt = _build_judge_prompt("p", "o", "b", rubric)
        for criterion in rubric.criteria:
            assert criterion.name in prompt


class TestParseJudgeResponse:

    def _rubric(self) -> Rubric:
        return Rubric(
            task_type=TaskType.CODE_GENERATION,
            version="1.0",
            criteria=[
                RubricCriterion(name="correctness", weight=0.5, prompt="..."),
                RubricCriterion(name="style", weight=0.3, prompt="..."),
                RubricCriterion(name="completeness", weight=0.2, prompt="..."),
            ],
        )

    def test_valid_json(self) -> None:
        rubric = self._rubric()
        response = json.dumps({"correctness": 0.9, "style": 0.8, "completeness": 0.7})
        scores = _parse_judge_response(response, rubric)
        assert scores == {"correctness": 0.9, "style": 0.8, "completeness": 0.7}

    def test_json_with_markdown_fences(self) -> None:
        rubric = self._rubric()
        response = '```json\n{"correctness": 0.95, "style": 0.85, "completeness": 0.75}\n```'
        scores = _parse_judge_response(response, rubric)
        assert scores["correctness"] == pytest.approx(0.95)
        assert scores["style"] == pytest.approx(0.85)
        assert scores["completeness"] == pytest.approx(0.75)

    def test_json_with_plain_fences(self) -> None:
        rubric = self._rubric()
        response = '```\n{"correctness": 0.9, "style": 0.8, "completeness": 0.7}\n```'
        scores = _parse_judge_response(response, rubric)
        assert scores["correctness"] == pytest.approx(0.9)

    def test_clamps_scores_to_valid_range(self) -> None:
        rubric = self._rubric()
        response = json.dumps({"correctness": 1.5, "style": -0.3, "completeness": 0.5})
        scores = _parse_judge_response(response, rubric)
        assert scores["correctness"] == 1.0
        assert scores["style"] == 0.0
        assert scores["completeness"] == 0.5

    def test_missing_criterion_defaults_to_zero(self) -> None:
        rubric = self._rubric()
        response = json.dumps({"correctness": 0.9})
        scores = _parse_judge_response(response, rubric)
        assert scores["correctness"] == 0.9
        assert scores["style"] == 0.0
        assert scores["completeness"] == 0.0

    def test_garbage_response_defaults_to_zeros(self) -> None:
        rubric = self._rubric()
        scores = _parse_judge_response("this is not json at all", rubric)
        assert all(v == 0.0 for v in scores.values())

    def test_empty_response_defaults_to_zeros(self) -> None:
        rubric = self._rubric()
        scores = _parse_judge_response("", rubric)
        assert all(v == 0.0 for v in scores.values())


class TestComputeWeightedScore:

    def test_weighted_calculation(self) -> None:
        rubric = Rubric(
            task_type=TaskType.CODE_GENERATION,
            version="1.0",
            criteria=[
                RubricCriterion(name="a", weight=0.6, prompt="..."),
                RubricCriterion(name="b", weight=0.4, prompt="..."),
            ],
        )
        scores = {"a": 1.0, "b": 0.5}
        result = compute_weighted_score(scores, rubric)
        assert result == pytest.approx(0.8)

    def test_perfect_score(self) -> None:
        rubric = get_rubric(TaskType.CLASSIFICATION)
        assert rubric is not None
        scores = {c.name: 1.0 for c in rubric.criteria}
        assert compute_weighted_score(scores, rubric) == pytest.approx(1.0)

    def test_zero_score(self) -> None:
        rubric = get_rubric(TaskType.CLASSIFICATION)
        assert rubric is not None
        scores = {c.name: 0.0 for c in rubric.criteria}
        assert compute_weighted_score(scores, rubric) == pytest.approx(0.0)

    def test_result_clamped_to_unit_interval(self) -> None:
        rubric = Rubric(
            task_type=TaskType.CODE_GENERATION,
            version="1.0",
            criteria=[
                RubricCriterion(name="x", weight=1.0, prompt="..."),
            ],
        )
        # Even if a criterion score were somehow >1 after clamping, the
        # weighted sum should still be <= 1
        assert compute_weighted_score({"x": 1.0}, rubric) <= 1.0


class TestEvaluate:
    """Integration-style tests for the full evaluate() flow with mocked LLM."""

    @pytest.mark.asyncio
    async def test_evaluate_returns_score_and_version(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "correctness": 0.9,
            "style": 0.8,
            "completeness": 0.7,
        })

        with patch("blockthrough.benchmarking.judge.litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            score, version = await evaluate(
                original_prompt="Write a sort function",
                original_completion="def sort(x): return sorted(x)",
                benchmark_completion="def sort(lst): return sorted(lst)",
                task_type=TaskType.CODE_GENERATION,
                judge_model="claude-haiku-4-5-20251001",
            )

        assert 0.0 <= score <= 1.0
        expected = 0.9 * 0.5 + 0.8 * 0.3 + 0.7 * 0.2
        assert score == pytest.approx(expected)
        assert version == RUBRIC_VERSION

        mock_llm.assert_awaited_once()
        call_kwargs = mock_llm.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"
        assert call_kwargs.kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_raises_for_unknown_task_type(self) -> None:
        with pytest.raises(ValueError, match="No rubric defined"):
            await evaluate(
                original_prompt="hello",
                original_completion="hi",
                benchmark_completion="hey",
                task_type=TaskType.UNKNOWN,
            )

    @pytest.mark.asyncio
    async def test_evaluate_handles_malformed_judge_response(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "I cannot score this properly."

        with patch("blockthrough.benchmarking.judge.litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            score, version = await evaluate(
                original_prompt="Classify this email",
                original_completion="spam",
                benchmark_completion="not spam",
                task_type=TaskType.CLASSIFICATION,
            )

        # Malformed response => all criteria default to 0.0
        assert score == pytest.approx(0.0)
        assert version == RUBRIC_VERSION

    @pytest.mark.asyncio
    async def test_evaluate_classification_perfect_match(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"accuracy": 1.0})

        with patch("blockthrough.benchmarking.judge.litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            score, _ = await evaluate(
                original_prompt="Classify sentiment",
                original_completion="positive",
                benchmark_completion="positive",
                task_type=TaskType.CLASSIFICATION,
            )

        assert score == pytest.approx(1.0)
