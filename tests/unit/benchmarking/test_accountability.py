"""Tests for the accountability report generation module.

All DB access is mocked. Tests verify cost impact estimation, report structure,
and attestation hash determinism.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from blockthrough.benchmarking.accountability import (
    AccountabilityReport,
    DriftItem,
    _compute_cost_impact,
    _hash_report_data,
    generate_report,
)
from blockthrough.benchmarking.drift import DriftReport


class TestComputeCostImpact:
    """Unit tests for the cost impact estimation formula."""

    def test_basic_cost_impact(self) -> None:
        """10% degradation on 100 calls at $0.01 = $0.10 impact."""
        impact = _compute_cost_impact(
            call_volume=100,
            avg_cost_per_call=0.01,
            delta_pct=10.0,
        )
        assert impact == pytest.approx(0.10, abs=1e-6)

    def test_zero_volume_no_impact(self) -> None:
        """No calls means no cost impact regardless of degradation."""
        impact = _compute_cost_impact(
            call_volume=0,
            avg_cost_per_call=0.05,
            delta_pct=50.0,
        )
        assert impact == 0.0

    def test_zero_cost_no_impact(self) -> None:
        """Free API calls have no cost impact."""
        impact = _compute_cost_impact(
            call_volume=1000,
            avg_cost_per_call=0.0,
            delta_pct=20.0,
        )
        assert impact == 0.0

    def test_large_degradation(self) -> None:
        """50% degradation should yield half the total spend as impact."""
        impact = _compute_cost_impact(
            call_volume=200,
            avg_cost_per_call=0.05,
            delta_pct=50.0,
        )
        # 200 * 0.05 * 50/100 = 5.0
        assert impact == pytest.approx(5.0, abs=1e-6)

    def test_small_degradation(self) -> None:
        """5.1% degradation should yield ~5.1% of spend as impact."""
        impact = _compute_cost_impact(
            call_volume=1000,
            avg_cost_per_call=0.001,
            delta_pct=5.1,
        )
        # 1000 * 0.001 * 5.1/100 = 0.051
        assert impact == pytest.approx(0.051, abs=1e-6)


def _hash_from_report(report: AccountabilityReport) -> str:
    """Test helper: call _hash_report_data with report fields."""
    return _hash_report_data(
        report.org_id, report.generated_at, report.drift_items, report.estimated_total_cost_impact
    )


class TestHashReport:
    """Verify attestation hash determinism and structure."""

    def _make_report(self, **overrides: object) -> AccountabilityReport:
        defaults = {
            "org_id": "test-org",
            "generated_at": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            "drift_items": [],
            "estimated_total_cost_impact": 0.0,
            "attestation_hash": "",
        }
        defaults.update(overrides)
        return AccountabilityReport(**defaults)  # type: ignore[arg-type]

    def test_deterministic_hash(self) -> None:
        """Same inputs should always produce the same hash."""
        r1 = self._make_report()
        r2 = self._make_report()
        assert _hash_from_report(r1) == _hash_from_report(r2)

    def test_different_org_different_hash(self) -> None:
        r1 = self._make_report(org_id="org-a")
        r2 = self._make_report(org_id="org-b")
        assert _hash_from_report(r1) != _hash_from_report(r2)

    def test_different_drift_items_different_hash(self) -> None:
        item_a = DriftItem(
            model="claude-sonnet-4",
            task_type="code_generation",
            baseline_quality=0.90,
            current_quality=0.75,
            delta_pct=16.67,
            p_value=0.001,
            confidence_interval=(0.10, 0.20),
            baseline_sample_size=50,
            current_sample_size=20,
            call_volume=100,
            avg_cost_per_call=0.01,
            estimated_cost_impact=0.1667,
        )
        item_b = DriftItem(
            model="gpt-4o",
            task_type="summarization",
            baseline_quality=0.85,
            current_quality=0.70,
            delta_pct=17.65,
            p_value=0.002,
            confidence_interval=(0.10, 0.20),
            baseline_sample_size=40,
            current_sample_size=15,
            call_volume=50,
            avg_cost_per_call=0.02,
            estimated_cost_impact=0.1765,
        )
        r1 = self._make_report(drift_items=[item_a])
        r2 = self._make_report(drift_items=[item_b])
        assert _hash_from_report(r1) != _hash_from_report(r2)

    def test_hash_is_hex_string(self) -> None:
        report = self._make_report()
        h = _hash_from_report(report)
        assert isinstance(h, str)
        assert len(h) == 64
        int(h, 16)  # Should not raise


class TestGenerateReport:
    """End-to-end report generation with mocked DB and attestation provider."""

    def _make_drift_report(self, **overrides: object) -> DriftReport:
        defaults = {
            "model": "claude-sonnet-4",
            "task_type": "code_generation",
            "baseline_quality": 0.90,
            "current_quality": 0.75,
            "delta_pct": 16.67,
            "p_value": 0.001,
            "confidence_interval": (0.10, 0.20),
            "baseline_sample_size": 50,
            "current_sample_size": 20,
            "first_detected_at": datetime(2026, 3, 1, tzinfo=UTC),
        }
        defaults.update(overrides)
        return DriftReport(**defaults)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_generate_report_with_single_drift(self) -> None:
        drift = self._make_drift_report()

        mock_session = AsyncMock()
        # _bulk_get_call_volumes returns rows with _mapping dicts
        mock_row = MagicMock()
        mock_row._mapping = {"model": "claude-sonnet-4", "task_type": "code_generation", "call_count": 500, "avg_cost": 0.02}
        volume_result = MagicMock()
        volume_result.fetchall.return_value = [mock_row]
        mock_session.execute = AsyncMock(return_value=volume_result)

        report = await generate_report(mock_session, [drift], "test-org")

        assert report.org_id == "test-org"
        assert len(report.drift_items) == 1
        assert report.drift_items[0].model == "claude-sonnet-4"
        assert report.drift_items[0].call_volume == 500
        assert report.drift_items[0].avg_cost_per_call == 0.02
        assert report.drift_items[0].estimated_cost_impact == pytest.approx(1.667, abs=0.01)
        assert report.estimated_total_cost_impact > 0
        assert report.attestation_hash
        assert len(report.attestation_hash) == 64

    @pytest.mark.asyncio
    async def test_generate_report_empty_drifts(self) -> None:
        mock_session = AsyncMock()

        report = await generate_report(mock_session, [], "test-org")

        assert report.org_id == "test-org"
        assert len(report.drift_items) == 0
        assert report.estimated_total_cost_impact == 0.0
        assert report.attestation_hash  # Still gets hashed

    @pytest.mark.asyncio
    async def test_generate_report_multiple_drifts(self) -> None:
        drift1 = self._make_drift_report(
            model="claude-sonnet-4",
            task_type="code_generation",
        )
        drift2 = self._make_drift_report(
            model="gpt-4o",
            task_type="summarization",
            delta_pct=8.5,
        )

        mock_session = AsyncMock()
        mock_row1 = MagicMock()
        mock_row1._mapping = {"model": "claude-sonnet-4", "task_type": "code_generation", "call_count": 200, "avg_cost": 0.01}
        mock_row2 = MagicMock()
        mock_row2._mapping = {"model": "gpt-4o", "task_type": "summarization", "call_count": 300, "avg_cost": 0.005}
        volume_result = MagicMock()
        volume_result.fetchall.return_value = [mock_row1, mock_row2]
        mock_session.execute = AsyncMock(return_value=volume_result)

        report = await generate_report(mock_session, [drift1, drift2], "multi-org")

        assert len(report.drift_items) == 2
        assert report.estimated_total_cost_impact > 0
        # Impact from drift1: 200 * 0.01 * 16.67/100 ~= 0.3334
        # Impact from drift2: 300 * 0.005 * 8.5/100 ~= 0.1275
        # Total ~= 0.4609
        total = sum(item.estimated_cost_impact for item in report.drift_items)
        assert report.estimated_total_cost_impact == pytest.approx(total, abs=1e-4)

    @pytest.mark.asyncio
    async def test_generate_report_zero_volume(self) -> None:
        """Drift detected but no production traffic -> zero cost impact."""
        drift = self._make_drift_report()

        mock_session = AsyncMock()
        volume_result = MagicMock()
        volume_result.fetchone.return_value = (0, 0.0)
        mock_session.execute = AsyncMock(return_value=volume_result)

        report = await generate_report(mock_session, [drift], "quiet-org")

        assert report.drift_items[0].call_volume == 0
        assert report.drift_items[0].estimated_cost_impact == 0.0
        assert report.estimated_total_cost_impact == 0.0

    @pytest.mark.asyncio
    async def test_report_hash_changes_with_content(self) -> None:
        """Two reports with different drifts should have different hashes."""
        drift_a = self._make_drift_report(delta_pct=10.0)
        drift_b = self._make_drift_report(delta_pct=20.0)

        mock_session = AsyncMock()
        result_a = MagicMock()
        result_a.fetchone.return_value = (100, 0.01)
        result_b = MagicMock()
        result_b.fetchone.return_value = (100, 0.01)

        mock_session.execute = AsyncMock(side_effect=[result_a])
        report_a = await generate_report(mock_session, [drift_a], "org-1")

        mock_session.execute = AsyncMock(side_effect=[result_b])
        report_b = await generate_report(mock_session, [drift_b], "org-1")

        assert report_a.attestation_hash != report_b.attestation_hash
