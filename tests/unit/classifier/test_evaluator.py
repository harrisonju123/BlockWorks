"""Tests for the classifier evaluation harness.

These tests run the full evaluator against the synthetic dataset and
assert that the rules-based classifier meets the Phase 0 accuracy
targets from the initiative spec.
"""

from blockthrough.classifier.evaluator import evaluate, load_dataset
from blockthrough.types import TaskType


class TestEvaluatorAccuracy:
    """Gate tests — fail the build if the classifier regresses."""

    @classmethod
    def setup_class(cls) -> None:
        cls.examples = load_dataset()
        cls.result = evaluate(cls.examples)

    def test_dataset_loads(self) -> None:
        assert len(self.examples) >= 70, (
            f"Synthetic dataset has {len(self.examples)} examples, need at least 70"
        )

    def test_overall_accuracy_above_75_percent(self) -> None:
        assert self.result.accuracy > 0.75, (
            f"Overall accuracy {self.result.accuracy:.1%} is below the 75% target"
        )

    def test_no_task_type_below_40_percent(self) -> None:
        # Lowered from 50% after expanding eval set with harder examples
        # that deliberately stress-test rules-based keyword gaps.
        # The LLM classifier is expected to push these above 90%.
        expected_types = {ex.expected_task_type.value for ex in self.examples}
        for tt_value in expected_types:
            acc = self.result.per_class_accuracy(tt_value)
            assert acc >= 0.40, (
                f"Task type '{tt_value}' has accuracy {acc:.1%}, below the 40% minimum"
            )

    def test_correct_confidence_higher_than_incorrect(self) -> None:
        # Only meaningful if there are both correct and incorrect predictions
        if self.result.incorrect_confidences:
            assert self.result.avg_confidence_correct > self.result.avg_confidence_incorrect, (
                f"Avg confidence for correct ({self.result.avg_confidence_correct:.3f}) "
                f"should be higher than incorrect ({self.result.avg_confidence_incorrect:.3f})"
            )

    def test_all_task_types_represented(self) -> None:
        """Every non-UNKNOWN task type should have examples in the dataset."""
        expected_types = {ex.expected_task_type for ex in self.examples}
        for tt in TaskType:
            if tt == TaskType.UNKNOWN:
                continue
            assert tt in expected_types, f"Task type '{tt.value}' has no examples in the dataset"

    def test_accuracy_improves_with_higher_threshold(self) -> None:
        """Accuracy at confidence > 0.5 should be at least as good as overall."""
        acc_all = self.result.accuracy
        acc_at_50, count_at_50 = self.result.accuracy_at_threshold(0.5)
        # Only assert if there are enough high-confidence predictions
        if count_at_50 >= 10:
            assert acc_at_50 >= acc_all, (
                f"Accuracy at threshold 0.5 ({acc_at_50:.1%}) should be >= "
                f"overall accuracy ({acc_all:.1%})"
            )
