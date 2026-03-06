"""Tests for eval report rendering."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from blockthrough.classifier.evaluator import CalibrationBucket
from blockthrough.eval.e2e_eval import E2EReport, PairedResult, ParetoPoint
from blockthrough.eval.report import (
    render_calibration,
    render_e2e_report,
    render_pareto,
    render_simulation_report,
)
from blockthrough.eval.types import (
    ExpectedBehavior,
    RoutingExpectation,
    SimulationReport,
    SimulationRow,
    TaskBreakdown,
)


def _capture_console() -> Console:
    return Console(file=StringIO(), force_terminal=True)


class TestRenderSimulationReport:

    def test_renders_without_error(self) -> None:
        report = SimulationReport(
            total=2,
            behavior_matches=1,
            behavior_accuracy=0.5,
            override_rate=0.5,
            avg_cost_delta=-0.001,
            quality_risk_count=0,
            per_task_breakdown={"classification": TaskBreakdown(override_count=1, passthrough_count=1, total=2)},
            rows=[
                SimulationRow(
                    expectation=RoutingExpectation(
                        task_type="classification",
                        requested_model="opus",
                        expected_behavior=ExpectedBehavior.OVERRIDE,
                    ),
                    decision_model="haiku",
                    decision_reason="rule[0]",
                    decision_was_overridden=True,
                    behavior_match=True,
                    cost_delta=-0.01,
                ),
                SimulationRow(
                    expectation=RoutingExpectation(
                        task_type="classification",
                        requested_model="haiku",
                        expected_behavior=ExpectedBehavior.OVERRIDE,
                        description="should override but didn't",
                    ),
                    decision_model="haiku",
                    decision_reason="passthrough",
                    decision_was_overridden=False,
                    behavior_match=False,
                    cost_delta=0.0,
                ),
            ],
        )
        con = _capture_console()
        render_simulation_report(report, console=con)
        output = con.file.getvalue()
        assert "Behavior accuracy" in output
        assert "Mismatches" in output


class TestRenderCalibration:

    def test_renders_without_error(self) -> None:
        buckets = [
            CalibrationBucket(bin_start=0.0, bin_end=0.5, avg_confidence=0.3, accuracy=0.4, count=10),
            CalibrationBucket(bin_start=0.5, bin_end=1.0, avg_confidence=0.8, accuracy=0.9, count=20),
        ]
        con = _capture_console()
        render_calibration(buckets, console=con)
        output = con.file.getvalue()
        assert "Calibration" in output


class TestRenderE2E:

    def test_renders_without_error(self) -> None:
        report = E2EReport(
            total=1,
            overridden_count=1,
            avg_quality_delta=-0.1,
            quality_degradation_count=1,
            total_cost_savings=-0.005,
            results=[
                PairedResult(
                    prompt_index=0, task_type="classification",
                    requested_model="opus", routed_model="haiku",
                    requested_score=0.9, routed_score=0.8, quality_delta=-0.1,
                    cost_requested=0.045, cost_routed=0.0024, cost_delta=-0.0426,
                    was_overridden=True,
                ),
            ],
        )
        con = _capture_console()
        render_e2e_report(report, console=con)
        output = con.file.getvalue()
        assert "End-to-End" in output


class TestRenderPareto:

    def test_renders_without_error(self) -> None:
        points = [
            ParetoPoint(model="opus", avg_quality=0.95, avg_cost=0.045, is_frontier=True),
            ParetoPoint(model="haiku", avg_quality=0.7, avg_cost=0.0024, is_frontier=True),
        ]
        con = _capture_console()
        render_pareto(points, console=con)
        output = con.file.getvalue()
        assert "Pareto" in output
