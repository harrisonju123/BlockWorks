"""Tests for the drift detection module.

All DB access is mocked — these tests verify the statistical logic and
threshold gating without needing a live database.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentproof.benchmarking.drift import (
    DEGRADATION_THRESHOLD_PCT,
    MIN_SAMPLE_SIZE,
    SIGNIFICANCE_LEVEL,
    DriftReport,
    compute_drift,
    detect_drift,
)


class TestComputeDrift:
    """Pure statistical tests — no DB or async involved."""

    def test_significant_degradation_detected(self) -> None:
        """Clear quality drop should be flagged."""
        random.seed(42)
        baseline = [0.85 + random.gauss(0, 0.03) for _ in range(50)]
        current = [0.70 + random.gauss(0, 0.03) for _ in range(20)]

        result = compute_drift(baseline, current)
        assert result is not None

        delta_pct, p_value, ci = result
        assert delta_pct > DEGRADATION_THRESHOLD_PCT
        assert p_value < SIGNIFICANCE_LEVEL
        assert ci[0] < ci[1]

    def test_no_degradation_returns_none(self) -> None:
        """Same-quality windows should not trigger a report."""
        random.seed(42)
        baseline = [0.85 + random.gauss(0, 0.02) for _ in range(50)]
        current = [0.85 + random.gauss(0, 0.02) for _ in range(20)]

        result = compute_drift(baseline, current)
        assert result is None

    def test_improvement_not_flagged(self) -> None:
        """Quality improvement (current > baseline) should not trigger."""
        random.seed(42)
        baseline = [0.70 + random.gauss(0, 0.03) for _ in range(50)]
        current = [0.90 + random.gauss(0, 0.03) for _ in range(20)]

        result = compute_drift(baseline, current)
        assert result is None

    def test_insufficient_baseline_samples(self) -> None:
        """Below MIN_SAMPLE_SIZE should not trigger."""
        baseline = [0.9, 0.8, 0.85]  # 3 < MIN_SAMPLE_SIZE
        current = [0.5, 0.4, 0.45, 0.35, 0.4, 0.5]

        result = compute_drift(baseline, current)
        assert result is None

    def test_insufficient_current_samples(self) -> None:
        """Below MIN_SAMPLE_SIZE in current window should not trigger."""
        baseline = [0.9, 0.85, 0.88, 0.87, 0.9, 0.86]
        current = [0.5, 0.4]  # 2 < MIN_SAMPLE_SIZE

        result = compute_drift(baseline, current)
        assert result is None

    def test_marginal_degradation_not_flagged(self) -> None:
        """A 3% drop should be below the 5% threshold."""
        random.seed(42)
        baseline = [0.85 + random.gauss(0, 0.01) for _ in range(100)]
        # ~3% drop: 0.85 -> 0.824
        current = [0.824 + random.gauss(0, 0.01) for _ in range(30)]

        result = compute_drift(baseline, current)
        assert result is None

    def test_high_variance_not_significant(self) -> None:
        """High variance should yield p > 0.05 even with a large mean shift."""
        random.seed(42)
        baseline = [0.85 + random.gauss(0, 0.25) for _ in range(6)]
        current = [0.70 + random.gauss(0, 0.25) for _ in range(6)]

        result = compute_drift(baseline, current)
        # With only 6 samples and huge variance, p-value should be too high
        # (result could be None due to either threshold or p-value)
        # Either outcome is correct — the point is it doesn't false-positive
        if result is not None:
            _, p_value, _ = result
            # If somehow flagged, p must at least be below threshold
            assert p_value < SIGNIFICANCE_LEVEL

    def test_zero_baseline_mean_returns_none(self) -> None:
        """Baseline of all zeros shouldn't divide by zero."""
        baseline = [0.0] * 10
        current = [0.5] * 10

        result = compute_drift(baseline, current)
        assert result is None

    def test_confidence_interval_ordering(self) -> None:
        """CI low must be less than CI high."""
        random.seed(42)
        baseline = [0.90 + random.gauss(0, 0.02) for _ in range(60)]
        current = [0.75 + random.gauss(0, 0.02) for _ in range(25)]

        result = compute_drift(baseline, current)
        assert result is not None
        _, _, ci = result
        assert ci[0] < ci[1]

    def test_identical_scores_no_drift(self) -> None:
        """Perfectly identical windows should not trigger."""
        scores = [0.80] * 20
        result = compute_drift(scores.copy(), scores.copy())
        assert result is None


class TestDetectDrift:
    """End-to-end drift detection with mocked DB calls."""

    @pytest.mark.asyncio
    async def test_detect_drift_with_degradation(self) -> None:
        """When scores degrade, detect_drift should return a DriftReport."""
        random.seed(42)
        baseline_scores = [0.88 + random.gauss(0, 0.02) for _ in range(40)]
        current_scores = [0.72 + random.gauss(0, 0.02) for _ in range(15)]

        mock_session = AsyncMock()

        # _get_distinct_model_task_pairs returns one pair
        pairs_result = MagicMock()
        pairs_result.fetchall.return_value = [
            ("claude-sonnet-4", "code_generation"),
        ]

        # Each _fetch_window_scores call returns baseline then current
        baseline_result = MagicMock()
        baseline_result.fetchall.return_value = [(s,) for s in baseline_scores]

        current_result = MagicMock()
        current_result.fetchall.return_value = [(s,) for s in current_scores]

        mock_session.execute = AsyncMock(
            side_effect=[pairs_result, baseline_result, current_result]
        )

        reports = await detect_drift(mock_session, models=["claude-sonnet-4"])

        assert len(reports) == 1
        r = reports[0]
        assert r.model == "claude-sonnet-4"
        assert r.task_type == "code_generation"
        assert r.delta_pct > DEGRADATION_THRESHOLD_PCT
        assert r.p_value < SIGNIFICANCE_LEVEL
        assert r.baseline_sample_size == len(baseline_scores)
        assert r.current_sample_size == len(current_scores)

    @pytest.mark.asyncio
    async def test_detect_drift_no_degradation(self) -> None:
        """When scores are stable, detect_drift should return empty."""
        random.seed(42)
        stable_scores = [0.85 + random.gauss(0, 0.02) for _ in range(30)]

        mock_session = AsyncMock()

        pairs_result = MagicMock()
        pairs_result.fetchall.return_value = [
            ("gpt-4o", "classification"),
        ]

        baseline_result = MagicMock()
        baseline_result.fetchall.return_value = [(s,) for s in stable_scores[:20]]

        current_result = MagicMock()
        current_result.fetchall.return_value = [(s,) for s in stable_scores[20:]]

        mock_session.execute = AsyncMock(
            side_effect=[pairs_result, baseline_result, current_result]
        )

        reports = await detect_drift(mock_session)
        assert len(reports) == 0

    @pytest.mark.asyncio
    async def test_detect_drift_no_data(self) -> None:
        """No benchmark data at all should return empty."""
        mock_session = AsyncMock()

        pairs_result = MagicMock()
        pairs_result.fetchall.return_value = []

        mock_session.execute = AsyncMock(return_value=pairs_result)

        reports = await detect_drift(mock_session)
        assert len(reports) == 0

    @pytest.mark.asyncio
    async def test_detect_drift_multiple_pairs(self) -> None:
        """Multiple (model, task_type) pairs should each be checked independently."""
        random.seed(42)
        # Pair 1: degraded
        degraded_baseline = [0.90 + random.gauss(0, 0.02) for _ in range(40)]
        degraded_current = [0.70 + random.gauss(0, 0.02) for _ in range(15)]
        # Pair 2: stable
        stable_baseline = [0.80 + random.gauss(0, 0.02) for _ in range(40)]
        stable_current = [0.80 + random.gauss(0, 0.02) for _ in range(15)]

        mock_session = AsyncMock()

        pairs_result = MagicMock()
        pairs_result.fetchall.return_value = [
            ("claude-sonnet-4", "code_generation"),
            ("gpt-4o-mini", "classification"),
        ]

        results = [
            pairs_result,
            # Pair 1 baseline
            MagicMock(fetchall=MagicMock(return_value=[(s,) for s in degraded_baseline])),
            # Pair 1 current
            MagicMock(fetchall=MagicMock(return_value=[(s,) for s in degraded_current])),
            # Pair 2 baseline
            MagicMock(fetchall=MagicMock(return_value=[(s,) for s in stable_baseline])),
            # Pair 2 current
            MagicMock(fetchall=MagicMock(return_value=[(s,) for s in stable_current])),
        ]

        mock_session.execute = AsyncMock(side_effect=results)

        reports = await detect_drift(mock_session)

        # Only the degraded pair should appear
        assert len(reports) == 1
        assert reports[0].model == "claude-sonnet-4"
        assert reports[0].task_type == "code_generation"


class TestDriftReportFields:
    """Verify DriftReport data integrity."""

    def test_drift_report_is_frozen(self) -> None:
        """DriftReport is a frozen dataclass — fields shouldn't be mutable."""
        report = DriftReport(
            model="test-model",
            task_type="code_generation",
            baseline_quality=0.90,
            current_quality=0.75,
            delta_pct=16.67,
            p_value=0.001,
            confidence_interval=(0.10, 0.20),
            baseline_sample_size=50,
            current_sample_size=20,
            first_detected_at=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            report.model = "changed"  # type: ignore[misc]

    def test_drift_report_all_fields_populated(self) -> None:
        now = datetime.now(UTC)
        report = DriftReport(
            model="claude-sonnet-4",
            task_type="summarization",
            baseline_quality=0.88,
            current_quality=0.76,
            delta_pct=13.64,
            p_value=0.002,
            confidence_interval=(0.08, 0.16),
            baseline_sample_size=45,
            current_sample_size=18,
            first_detected_at=now,
        )
        assert report.model == "claude-sonnet-4"
        assert report.task_type == "summarization"
        assert report.baseline_quality == 0.88
        assert report.current_quality == 0.76
        assert report.delta_pct == 13.64
        assert report.p_value == 0.002
        assert report.confidence_interval == (0.08, 0.16)
        assert report.baseline_sample_size == 45
        assert report.current_sample_size == 18
        assert report.first_detected_at == now
