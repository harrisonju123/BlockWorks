"""Tests for the EMA feedback adjuster."""

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.feedback.adjuster import (
    apply_feedback_adjustments,
    compute_feedback_adjustments,
)


def _entry(model: str, task: str, quality: float) -> FitnessEntry:
    return FitnessEntry(
        task_type=task, model=model,
        avg_quality=quality, avg_cost=0.01, avg_latency=500.0, sample_size=10,
    )


class TestComputeFeedbackAdjustments:
    def test_basic_adjustment(self):
        rows = [{"model": "m1", "task_type": "t1", "avg_delta": -0.10, "sample_count": 30}]
        result = compute_feedback_adjustments(rows, min_samples=20)
        assert ("m1", "t1") in result
        assert result[("m1", "t1")] < 0

    def test_below_min_samples_ignored(self):
        rows = [{"model": "m1", "task_type": "t1", "avg_delta": -0.10, "sample_count": 5}]
        result = compute_feedback_adjustments(rows, min_samples=20)
        assert len(result) == 0

    def test_clamped_to_max_adjustment(self):
        rows = [{"model": "m1", "task_type": "t1", "avg_delta": -0.50, "sample_count": 100}]
        result = compute_feedback_adjustments(rows, max_adjustment=0.15)
        assert result[("m1", "t1")] >= -0.15

    def test_ema_smoothing(self):
        """Multiple rounds of feedback should converge via EMA."""
        ema_state: dict[tuple[str, str], float] = {}
        rows = [{"model": "m1", "task_type": "t1", "avg_delta": -0.10, "sample_count": 30}]

        # First round
        compute_feedback_adjustments(rows, alpha=0.5, ema_state=ema_state)
        first = ema_state[("m1", "t1")]

        # Second round with same data
        compute_feedback_adjustments(rows, alpha=0.5, ema_state=ema_state)
        second = ema_state[("m1", "t1")]

        # EMA should be moving toward -0.10
        assert abs(second) > abs(first)

    def test_positive_feedback(self):
        rows = [{"model": "m1", "task_type": "t1", "avg_delta": 0.05, "sample_count": 30}]
        result = compute_feedback_adjustments(rows)
        assert result[("m1", "t1")] > 0

    def test_empty_feedback(self):
        result = compute_feedback_adjustments([])
        assert result == {}


class TestApplyFeedbackAdjustments:
    def test_basic_apply(self):
        entries = [_entry("m1", "t1", 0.80)]
        synthetic = [_entry("m1", "t1", 0.50)]
        adjustments = {("m1", "t1"): -0.05}
        result = apply_feedback_adjustments(entries, adjustments, synthetic)
        assert len(result) == 1
        assert result[0].avg_quality == pytest.approx(0.75)

    def test_floor_at_synthetic(self):
        """Adjusted quality never drops below synthetic baseline."""
        entries = [_entry("m1", "t1", 0.55)]
        synthetic = [_entry("m1", "t1", 0.50)]
        adjustments = {("m1", "t1"): -0.10}
        result = apply_feedback_adjustments(entries, adjustments, synthetic)
        assert result[0].avg_quality >= 0.50

    def test_cap_at_1(self):
        entries = [_entry("m1", "t1", 0.95)]
        synthetic = [_entry("m1", "t1", 0.50)]
        adjustments = {("m1", "t1"): 0.10}
        result = apply_feedback_adjustments(entries, adjustments, synthetic)
        assert result[0].avg_quality <= 1.0

    def test_no_adjustment_passthrough(self):
        entries = [_entry("m1", "t1", 0.80)]
        result = apply_feedback_adjustments(entries, {}, [])
        assert result[0].avg_quality == 0.80

    def test_preserves_other_fields(self):
        entries = [_entry("m1", "t1", 0.80)]
        synthetic = [_entry("m1", "t1", 0.50)]
        adjustments = {("m1", "t1"): -0.05}
        result = apply_feedback_adjustments(entries, adjustments, synthetic)
        assert result[0].model == "m1"
        assert result[0].task_type == "t1"
        assert result[0].avg_cost == 0.01
        assert result[0].avg_latency == 500.0
        assert result[0].sample_size == 10

    def test_multiple_entries(self):
        entries = [_entry("m1", "t1", 0.80), _entry("m2", "t1", 0.70)]
        synthetic = [_entry("m1", "t1", 0.50), _entry("m2", "t1", 0.45)]
        adjustments = {("m1", "t1"): -0.05, ("m2", "t1"): 0.03}
        result = apply_feedback_adjustments(entries, adjustments, synthetic)
        assert result[0].avg_quality == pytest.approx(0.75)
        assert result[1].avg_quality == pytest.approx(0.73)
