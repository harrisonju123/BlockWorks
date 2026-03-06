"""Evaluation harness for the task classifier.

Loads a synthetic JSONL dataset, runs each example through the
classifier (rules-based or LLM-based), and computes accuracy /
precision / recall / F1 metrics per task type plus a confusion matrix.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from blockthrough.classifier.llm_classifier import llm_classify
from blockthrough.classifier.rules import classify, compute_token_ratio, extract_keywords
from blockthrough.classifier.taxonomy import ClassificationResult, ClassifierInput
from blockthrough.types import TaskType

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DEFAULT_DATASET = _FIXTURES_DIR / "synthetic_prompts.jsonl"


@dataclass
class EvalExample:
    """One row from the synthetic dataset, ready for classification."""

    classifier_input: ClassifierInput
    expected_task_type: TaskType


@dataclass
class PerClassMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class EvalResult:
    """Aggregated evaluation metrics."""

    total: int = 0
    correct: int = 0
    per_class: dict[str, PerClassMetrics] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    correct_confidences: list[float] = field(default_factory=list)
    incorrect_confidences: list[float] = field(default_factory=list)
    # Per-example results for threshold analysis
    predictions: list[tuple[bool, float]] = field(default_factory=list)
    # Per-example labels: (predicted_label, expected_label, confidence)
    detailed_predictions: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0

    @property
    def avg_confidence_correct(self) -> float:
        return (
            sum(self.correct_confidences) / len(self.correct_confidences)
            if self.correct_confidences
            else 0.0
        )

    @property
    def avg_confidence_incorrect(self) -> float:
        return (
            sum(self.incorrect_confidences) / len(self.incorrect_confidences)
            if self.incorrect_confidences
            else 0.0
        )

    def accuracy_at_threshold(self, threshold: float) -> tuple[float, int]:
        """Return (accuracy, count) considering only predictions above the confidence threshold."""
        filtered = [(is_correct, conf) for is_correct, conf in self.predictions if conf >= threshold]
        if not filtered:
            return 0.0, 0
        n_correct = sum(1 for is_correct, _ in filtered if is_correct)
        return n_correct / len(filtered), len(filtered)

    def per_class_accuracy(self, task_type: str) -> float:
        """Accuracy for a single task type (recall, since it measures correct out of expected)."""
        m = self.per_class.get(task_type)
        if m is None:
            return 0.0
        return m.recall


def _extract_keywords(system_prompt: str) -> list[str]:
    """Scan system prompt text for classifier-relevant keywords."""
    return extract_keywords(system_prompt)


def load_dataset(path: Path | None = None) -> list[EvalExample]:
    """Load JSONL dataset and convert each row into a ClassifierInput."""
    dataset_path = path or _DEFAULT_DATASET
    examples: list[EvalExample] = []

    with dataset_path.open() as f:
        for line in f:
            row = json.loads(line)

            system_prompt = row["system_prompt"]
            user_prompt = row.get("user_prompt", "")
            prompt_tokens = row["prompt_tokens"]
            completion_tokens = row["completion_tokens"]

            token_ratio = compute_token_ratio(prompt_tokens, completion_tokens)

            keywords = _extract_keywords(system_prompt)
            user_keywords = extract_keywords(user_prompt) if user_prompt else []

            ci = ClassifierInput(
                system_prompt_hash=str(hash(system_prompt)),
                has_tools=row["has_tools"],
                tool_count=row["tool_count"],
                has_json_schema=row["has_json_schema"],
                has_code_fence_in_system=row["has_code_fence_in_system"],
                prompt_token_count=prompt_tokens,
                completion_token_count=completion_tokens,
                token_ratio=token_ratio,
                model=row["model"],
                system_prompt_keywords=keywords,
                user_prompt_keywords=user_keywords,
                has_tool_calls=row.get("has_tool_calls", False),
                output_format_hint=row.get("output_format_hint"),
            )

            expected = TaskType(row["expected_task_type"])
            examples.append(EvalExample(classifier_input=ci, expected_task_type=expected))

    return examples


def _init_result() -> EvalResult:
    """Create an EvalResult with per-class containers for every known task type."""
    result = EvalResult()
    for tt in TaskType:
        result.per_class[tt.value] = PerClassMetrics()
        result.confusion[tt.value] = defaultdict(int)
    return result


def _accumulate(result: EvalResult, predicted: str, expected: str, confidence: float) -> None:
    """Shared metrics accumulation after each classification."""
    result.total += 1
    is_correct = predicted == expected

    if is_correct:
        result.correct += 1
        result.correct_confidences.append(confidence)
    else:
        result.incorrect_confidences.append(confidence)

    result.predictions.append((is_correct, confidence))
    result.detailed_predictions.append((predicted, expected, confidence))
    result.confusion[expected][predicted] += 1

    if predicted == expected:
        result.per_class[expected].tp += 1
    else:
        result.per_class[predicted].fp += 1
        result.per_class[expected].fn += 1


def evaluate(examples: list[EvalExample]) -> EvalResult:
    """Run the classifier on every example and compute metrics."""
    result = _init_result()

    for ex in examples:
        classification = classify(ex.classifier_input)
        _accumulate(
            result,
            predicted=classification.task_type.value,
            expected=ex.expected_task_type.value,
            confidence=classification.confidence,
        )

    return result


def print_report(result: EvalResult, console: Console | None = None, label: str = "rules") -> None:
    """Print a formatted evaluation report to stdout."""
    con = console or Console()

    con.print(f"\n[bold underline]Classifier Evaluation Report ({label})[/bold underline]\n")

    # Overall accuracy
    con.print(f"  Total examples:  {result.total}")
    con.print(f"  Correct:         {result.correct}")
    con.print(f"  [bold]Accuracy:        {result.accuracy:.1%}[/bold]")
    con.print()

    # Per-class metrics table
    table = Table(title="Per-Task-Type Metrics")
    table.add_column("Task Type", style="cyan")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("F1", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("FP", justify="right")
    table.add_column("FN", justify="right")

    for tt in TaskType:
        m = result.per_class.get(tt.value)
        if m is None:
            continue
        table.add_row(
            tt.value,
            f"{m.precision:.2f}",
            f"{m.recall:.2f}",
            f"{m.f1:.2f}",
            str(m.tp),
            str(m.fp),
            str(m.fn),
        )

    con.print(table)
    con.print()

    # Confusion matrix
    task_types = [tt.value for tt in TaskType]
    cm_table = Table(title="Confusion Matrix (rows=expected, cols=predicted)")
    cm_table.add_column("", style="bold")
    for tt in task_types:
        cm_table.add_column(tt[:8], justify="right", min_width=5)

    for expected in task_types:
        row_vals: list[str] = []
        for predicted in task_types:
            count = result.confusion.get(expected, {}).get(predicted, 0)
            row_vals.append(str(count) if count > 0 else ".")
        cm_table.add_row(expected[:12], *row_vals)

    con.print(cm_table)
    con.print()

    # Confidence analysis
    con.print("[bold]Confidence Analysis[/bold]")
    con.print(f"  Avg confidence (correct):   {result.avg_confidence_correct:.3f}")
    con.print(f"  Avg confidence (incorrect): {result.avg_confidence_incorrect:.3f}")
    con.print()

    # Accuracy at thresholds
    threshold_table = Table(title="Accuracy at Confidence Thresholds")
    threshold_table.add_column("Threshold", justify="right")
    threshold_table.add_column("Accuracy", justify="right")
    threshold_table.add_column("Coverage", justify="right")

    for t in [0.0, 0.3, 0.5, 0.7, 0.9]:
        acc, count = result.accuracy_at_threshold(t)
        coverage = count / result.total if result.total > 0 else 0.0
        threshold_table.add_row(f">{t:.1f}", f"{acc:.1%}", f"{coverage:.1%} ({count})")

    con.print(threshold_table)
    con.print()


_FALLBACK_CLASSIFICATION = ClassificationResult(
    task_type=TaskType.UNKNOWN,
    confidence=0.0,
    signals=["llm_classifier_failed"],
)


async def evaluate_llm(examples: list[EvalExample], model: str) -> EvalResult:
    """Run the LLM classifier against every example and compute metrics."""
    result = _init_result()

    for ex in examples:
        try:
            classification = await llm_classify(ex.classifier_input, model=model)
        except Exception:
            classification = _FALLBACK_CLASSIFICATION

        _accumulate(
            result,
            predicted=classification.task_type.value,
            expected=ex.expected_task_type.value,
            confidence=classification.confidence,
        )

    return result


def run(dataset_path: Path | None = None, model: str | None = None) -> EvalResult:
    """Load dataset, evaluate, print report, and return result.

    If model is provided, uses the LLM classifier via that model.
    Otherwise uses the rules-based classifier.
    """
    examples = load_dataset(dataset_path)

    if model:
        result = asyncio.run(evaluate_llm(examples, model=model))
    else:
        result = evaluate(examples)

    print_report(result, label=model or "rules")
    return result


# --- Per-task accuracy targets, differentiated by routing impact ---

# Regression guard floors — set ~10% below current rules-based accuracy.
# These prevent silent degradation. LLM classifier aspirational targets are higher.
PER_TASK_ACCURACY_TARGETS: dict[str, float] = {
    "code_generation": 0.90,
    "code_review": 0.40,
    "reasoning": 0.40,
    "classification": 0.75,
    "extraction": 0.80,
    "summarization": 0.70,
    "conversation": 0.90,
    "tool_selection": 0.85,
    "architecture": 0.40,
    "debugging": 0.40,
    "refactoring": 0.40,
    "documentation": 0.40,
    "testing": 0.30,
}


# --- Classifier comparison and calibration ---

@dataclass
class DisagreementRow:
    """One example where two classifiers disagree."""

    index: int
    expected: str
    a_predicted: str
    a_confidence: float
    b_predicted: str
    b_confidence: float
    a_correct: bool
    b_correct: bool


@dataclass
class ComparisonReport:
    """Head-to-head comparison of two classifier runs."""

    a_accuracy: float
    b_accuracy: float
    agreement_rate: float
    disagreements: list[DisagreementRow]
    per_task_winner: dict[str, str]  # task_type -> "a" | "b" | "tie"


def compare(result_a: EvalResult, result_b: EvalResult) -> ComparisonReport:
    """Compare two EvalResults using their detailed_predictions.

    Both results must come from the same dataset (same length, same ordering).
    """
    if len(result_a.detailed_predictions) != len(result_b.detailed_predictions):
        raise ValueError(
            f"Results have different lengths: {len(result_a.detailed_predictions)} vs "
            f"{len(result_b.detailed_predictions)}"
        )

    disagreements: list[DisagreementRow] = []
    agreements = 0
    # task_type -> (a_correct_count, b_correct_count)
    per_task_scores: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for i, (a_pred, b_pred) in enumerate(
        zip(result_a.detailed_predictions, result_b.detailed_predictions)
    ):
        a_label, a_expected, a_conf = a_pred
        b_label, b_expected, b_conf = b_pred
        a_ok = a_label == a_expected
        b_ok = b_label == b_expected

        if a_ok:
            per_task_scores[a_expected][0] += 1
        if b_ok:
            per_task_scores[b_expected][1] += 1

        if a_label == b_label:
            agreements += 1
        else:
            disagreements.append(
                DisagreementRow(
                    index=i,
                    expected=a_expected,
                    a_predicted=a_label,
                    a_confidence=a_conf,
                    b_predicted=b_label,
                    b_confidence=b_conf,
                    a_correct=a_ok,
                    b_correct=b_ok,
                )
            )

    total = len(result_a.detailed_predictions)
    per_task_winner: dict[str, str] = {}
    for tt, (a_count, b_count) in per_task_scores.items():
        if a_count > b_count:
            per_task_winner[tt] = "a"
        elif b_count > a_count:
            per_task_winner[tt] = "b"
        else:
            per_task_winner[tt] = "tie"

    return ComparisonReport(
        a_accuracy=result_a.accuracy,
        b_accuracy=result_b.accuracy,
        agreement_rate=agreements / total if total > 0 else 0.0,
        disagreements=disagreements,
        per_task_winner=per_task_winner,
    )


@dataclass
class CalibrationBucket:
    """One bin of the confidence calibration curve."""

    bin_start: float
    bin_end: float
    avg_confidence: float
    accuracy: float
    count: int


def calibration_curve(result: EvalResult, n_buckets: int = 10) -> list[CalibrationBucket]:
    """Bucket predictions by confidence, compute actual accuracy per bucket."""
    if not result.detailed_predictions or n_buckets < 1:
        return []

    bin_width = 1.0 / n_buckets
    # bucket_index -> (sum_confidence, correct_count, total_count)
    buckets: dict[int, list[float | int]] = {}

    for predicted, expected, confidence in result.detailed_predictions:
        idx = min(int(confidence / bin_width), n_buckets - 1)
        if idx not in buckets:
            buckets[idx] = [0.0, 0, 0]
        buckets[idx][0] += confidence
        buckets[idx][1] += 1 if predicted == expected else 0
        buckets[idx][2] += 1

    out: list[CalibrationBucket] = []
    for idx in sorted(buckets.keys()):
        sum_conf, correct, total = buckets[idx]
        out.append(
            CalibrationBucket(
                bin_start=round(idx * bin_width, 2),
                bin_end=round((idx + 1) * bin_width, 2),
                avg_confidence=sum_conf / total,
                accuracy=correct / total,
                count=int(total),
            )
        )
    return out
