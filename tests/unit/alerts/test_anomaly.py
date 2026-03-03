"""Tests for anomaly detection: Z-score calculation, threshold logic, model switches, failure spikes."""

from __future__ import annotations

import math

import pytest

from agentproof.alerts.anomaly import (
    AnomalyResult,
    classify_spend_anomaly,
    compute_z_score,
    detect_failure_rate_spike,
    detect_model_switch,
)
from agentproof.alerts.types import AlertSeverity


class TestZScoreCalculation:

    def test_exact_mean_returns_zero(self) -> None:
        assert compute_z_score(100.0, 100.0, 10.0) == 0.0

    def test_one_stddev_above(self) -> None:
        assert compute_z_score(110.0, 100.0, 10.0) == pytest.approx(1.0)

    def test_two_stddev_above(self) -> None:
        assert compute_z_score(120.0, 100.0, 10.0) == pytest.approx(2.0)

    def test_negative_z_score(self) -> None:
        """Below-average spend should yield a negative z-score."""
        z = compute_z_score(80.0, 100.0, 10.0)
        assert z == pytest.approx(-2.0)

    def test_zero_stddev_returns_zero(self) -> None:
        """Flat baseline (no variance) should not produce false positives."""
        assert compute_z_score(200.0, 100.0, 0.0) == 0.0

    def test_nan_stddev_returns_zero(self) -> None:
        assert compute_z_score(200.0, 100.0, float("nan")) == 0.0

    def test_large_z_score(self) -> None:
        z = compute_z_score(1000.0, 100.0, 10.0)
        assert z == pytest.approx(90.0)


class TestClassifySpendAnomaly:

    def test_normal_spend_not_flagged(self) -> None:
        result = classify_spend_anomaly(105.0, 100.0, 10.0)
        assert not result.is_anomaly
        assert result.severity == AlertSeverity.INFO

    def test_warning_threshold(self) -> None:
        # z = (121 - 100) / 10 = 2.1 -> warning
        result = classify_spend_anomaly(121.0, 100.0, 10.0)
        assert result.is_anomaly
        assert result.severity == AlertSeverity.WARNING
        assert result.z_score == pytest.approx(2.1)

    def test_critical_threshold(self) -> None:
        # z = (131 - 100) / 10 = 3.1 -> critical
        result = classify_spend_anomaly(131.0, 100.0, 10.0)
        assert result.is_anomaly
        assert result.severity == AlertSeverity.CRITICAL

    def test_exactly_at_warning_boundary_is_not_anomaly(self) -> None:
        """z == 2.0 exactly should NOT trigger (strictly greater than)."""
        result = classify_spend_anomaly(120.0, 100.0, 10.0)
        assert not result.is_anomaly

    def test_just_above_warning_boundary(self) -> None:
        result = classify_spend_anomaly(120.01, 100.0, 10.0)
        assert result.is_anomaly
        assert result.severity == AlertSeverity.WARNING

    def test_exactly_at_critical_boundary_is_warning(self) -> None:
        """z == 3.0 exactly should be warning, not critical."""
        result = classify_spend_anomaly(130.0, 100.0, 10.0)
        assert result.is_anomaly
        assert result.severity == AlertSeverity.WARNING

    def test_custom_thresholds(self) -> None:
        result = classify_spend_anomaly(
            115.0, 100.0, 10.0, warning_z=1.0, critical_z=2.0
        )
        assert result.is_anomaly
        assert result.severity == AlertSeverity.WARNING

    def test_zero_stddev_not_flagged(self) -> None:
        """Flat history should never trigger anomaly alerts."""
        result = classify_spend_anomaly(999.0, 100.0, 0.0)
        assert not result.is_anomaly

    def test_message_contains_values(self) -> None:
        result = classify_spend_anomaly(131.0, 100.0, 10.0)
        assert "$131.00" in result.message
        assert "$100.00" in result.message


class TestDetectModelSwitch:

    def test_no_new_models(self) -> None:
        result = detect_model_switch(
            {"claude-sonnet-4-20250514", "gpt-4o"},
            {"claude-sonnet-4-20250514", "gpt-4o", "gpt-4o-mini"},
        )
        assert result is None

    def test_new_model_detected(self) -> None:
        result = detect_model_switch(
            {"claude-sonnet-4-20250514", "claude-opus-4-20250514"},
            {"claude-sonnet-4-20250514"},
        )
        assert result is not None
        assert result.is_anomaly
        assert result.severity == AlertSeverity.WARNING
        assert "claude-opus-4-20250514" in result.message

    def test_multiple_new_models(self) -> None:
        result = detect_model_switch(
            {"model-a", "model-b", "model-c"},
            {"model-a"},
        )
        assert result is not None
        assert "model-b" in result.message
        assert "model-c" in result.message

    def test_empty_baseline(self) -> None:
        result = detect_model_switch({"model-a"}, set())
        assert result is not None
        assert result.is_anomaly

    def test_empty_current(self) -> None:
        result = detect_model_switch(set(), {"model-a"})
        assert result is None


class TestDetectFailureRateSpike:

    def test_no_spike(self) -> None:
        result = detect_failure_rate_spike(0.02, 0.02)
        assert result is None

    def test_below_min_rate_ignored(self) -> None:
        """Low absolute failure rate should not fire even if ratio is high."""
        result = detect_failure_rate_spike(0.03, 0.01)
        assert result is None

    def test_spike_detected(self) -> None:
        # 20% current vs 5% baseline = 4x -> spike
        result = detect_failure_rate_spike(0.20, 0.05)
        assert result is not None
        assert result.is_anomaly
        assert "20.0%" in result.message

    def test_critical_spike(self) -> None:
        # 30% current vs 5% baseline = 6x -> critical
        result = detect_failure_rate_spike(0.30, 0.05)
        assert result is not None
        assert result.severity == AlertSeverity.CRITICAL

    def test_warning_spike(self) -> None:
        # 12% current vs 5% baseline = 2.4x -> warning (between 2x and 3x)
        result = detect_failure_rate_spike(0.12, 0.05)
        assert result is not None
        assert result.severity == AlertSeverity.WARNING

    def test_zero_baseline_with_failures(self) -> None:
        """If baseline had zero failures, any meaningful rate is a spike."""
        result = detect_failure_rate_spike(0.10, 0.0)
        assert result is not None
        assert result.is_anomaly

    def test_zero_baseline_below_floor(self) -> None:
        result = detect_failure_rate_spike(0.03, 0.0)
        assert result is None
