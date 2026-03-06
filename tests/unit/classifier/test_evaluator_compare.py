"""Tests for classifier comparison and calibration curve."""

from __future__ import annotations

from blockthrough.classifier.evaluator import (
    CalibrationBucket,
    ComparisonReport,
    EvalResult,
    PER_TASK_ACCURACY_TARGETS,
    calibration_curve,
    compare,
)
from blockthrough.types import TaskType


def _make_result(predictions: list[tuple[str, str, float]]) -> EvalResult:
    """Build an EvalResult from a list of (predicted, expected, confidence)."""
    result = EvalResult()
    for predicted, expected, confidence in predictions:
        is_correct = predicted == expected
        result.total += 1
        if is_correct:
            result.correct += 1
            result.correct_confidences.append(confidence)
        else:
            result.incorrect_confidences.append(confidence)
        result.predictions.append((is_correct, confidence))
        result.detailed_predictions.append((predicted, expected, confidence))
    return result


class TestCompare:

    def test_identical_results_full_agreement(self) -> None:
        preds = [
            ("classification", "classification", 0.9),
            ("code_generation", "code_generation", 0.8),
            ("reasoning", "reasoning", 0.7),
        ]
        result = _make_result(preds)

        report = compare(result, result)

        assert report.agreement_rate == 1.0
        assert len(report.disagreements) == 0

    def test_identifies_disagreements(self) -> None:
        preds_a = [
            ("classification", "classification", 0.9),
            ("reasoning", "code_generation", 0.6),  # wrong
            ("summarization", "summarization", 0.8),
        ]
        preds_b = [
            ("classification", "classification", 0.85),
            ("code_generation", "code_generation", 0.7),  # correct
            ("extraction", "summarization", 0.5),  # wrong
        ]
        result_a = _make_result(preds_a)
        result_b = _make_result(preds_b)

        report = compare(result_a, result_b)

        assert len(report.disagreements) == 2
        # First disagreement: index 1 where a predicted reasoning, b predicted code_gen
        d1 = report.disagreements[0]
        assert d1.index == 1
        assert d1.a_correct is False
        assert d1.b_correct is True
        # Second disagreement: index 2
        d2 = report.disagreements[1]
        assert d2.index == 2
        assert d2.a_correct is True
        assert d2.b_correct is False

    def test_per_task_winner(self) -> None:
        preds_a = [
            ("classification", "classification", 0.9),
            ("classification", "classification", 0.8),
            ("reasoning", "reasoning", 0.7),
        ]
        preds_b = [
            ("classification", "classification", 0.85),
            ("extraction", "classification", 0.5),  # wrong
            ("reasoning", "reasoning", 0.75),
        ]
        result_a = _make_result(preds_a)
        result_b = _make_result(preds_b)

        report = compare(result_a, result_b)

        assert report.per_task_winner["classification"] == "a"
        assert report.per_task_winner["reasoning"] == "tie"

    def test_mismatched_lengths_raises(self) -> None:
        result_a = _make_result([("a", "a", 0.9)])
        result_b = _make_result([("a", "a", 0.9), ("b", "b", 0.8)])

        try:
            compare(result_a, result_b)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestCalibrationCurve:

    def test_bucket_count(self) -> None:
        preds = [
            ("classification", "classification", 0.15),
            ("classification", "classification", 0.55),
            ("reasoning", "reasoning", 0.75),
            ("reasoning", "code_generation", 0.95),
        ]
        result = _make_result(preds)

        buckets = calibration_curve(result, n_buckets=10)

        # 4 predictions in 4 different decile buckets
        assert len(buckets) == 4
        assert all(b.count == 1 for b in buckets)

    def test_empty_result_returns_empty(self) -> None:
        result = EvalResult()

        buckets = calibration_curve(result)

        assert buckets == []

    def test_accuracy_computed_correctly(self) -> None:
        # All predictions in the 0.8-0.9 bucket
        preds = [
            ("classification", "classification", 0.85),  # correct
            ("reasoning", "reasoning", 0.82),  # correct
            ("extraction", "classification", 0.88),  # wrong
        ]
        result = _make_result(preds)

        buckets = calibration_curve(result, n_buckets=10)

        assert len(buckets) == 1
        assert buckets[0].count == 3
        assert abs(buckets[0].accuracy - 2.0 / 3.0) < 1e-9

    def test_single_bucket_mode(self) -> None:
        preds = [
            ("a", "a", 0.1),
            ("b", "b", 0.5),
            ("c", "c", 0.9),
        ]
        result = _make_result(preds)

        buckets = calibration_curve(result, n_buckets=1)

        assert len(buckets) == 1
        assert buckets[0].count == 3
        assert buckets[0].accuracy == 1.0


class TestPerTaskTargets:

    def test_all_non_unknown_types_have_targets(self) -> None:
        for tt in TaskType:
            if tt == TaskType.UNKNOWN:
                continue
            assert tt.value in PER_TASK_ACCURACY_TARGETS, (
                f"Missing accuracy target for {tt.value}"
            )

    def test_targets_are_reasonable_range(self) -> None:
        for tt, target in PER_TASK_ACCURACY_TARGETS.items():
            assert 0.3 <= target <= 1.0, f"Target for {tt} ({target}) outside [0.3, 1.0]"
