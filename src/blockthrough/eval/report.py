"""Unified report rendering for eval results."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from blockthrough.classifier.evaluator import CalibrationBucket, ComparisonReport
from blockthrough.eval.e2e_eval import E2EReport, ParetoPoint
from blockthrough.eval.types import PolicyComparison, SimulationReport


def render_simulation_report(report: SimulationReport, console: Console | None = None) -> None:
    con = console or Console()

    con.print("\n[bold underline]Routing Policy Simulation Report[/bold underline]\n")
    con.print(f"  Total scenarios:    {report.total}")
    con.print(f"  Behavior matches:   {report.behavior_matches}")
    con.print(f"  [bold]Behavior accuracy: {report.behavior_accuracy:.1%}[/bold]")
    con.print(f"  Override rate:      {report.override_rate:.1%}")
    con.print(f"  Avg cost delta:     {report.avg_cost_delta:+.6f}")
    con.print(f"  Quality risk count: {report.quality_risk_count}")
    con.print()

    table = Table(title="Per-Task Breakdown")
    table.add_column("Task Type", style="cyan")
    table.add_column("Override", justify="right")
    table.add_column("Passthrough", justify="right")
    table.add_column("Total", justify="right")

    for tt, bd in sorted(report.per_task_breakdown.items()):
        table.add_row(tt, str(bd.override_count), str(bd.passthrough_count), str(bd.total))

    con.print(table)
    con.print()

    # Show mismatches
    mismatches = [r for r in report.rows if not r.behavior_match]
    if mismatches:
        con.print(f"[bold red]Mismatches ({len(mismatches)}):[/bold red]")
        for r in mismatches:
            exp = r.expectation
            con.print(
                f"  {exp.description or exp.task_type}: "
                f"expected {exp.expected_behavior.value}, "
                f"got {'override' if r.decision_was_overridden else 'passthrough'} "
                f"-> {r.decision_model}"
            )
        con.print()


def render_comparison_report(comparison: PolicyComparison, console: Console | None = None) -> None:
    con = console or Console()

    con.print("\n[bold underline]Policy Comparison Report[/bold underline]\n")
    con.print(f"  Policy A accuracy:    {comparison.policy_a_report.behavior_accuracy:.1%}")
    con.print(f"  Policy B accuracy:    {comparison.policy_b_report.behavior_accuracy:.1%}")
    con.print(f"  Agreement rate:       {comparison.behavior_agreement_rate:.1%}")
    con.print(f"  Cost delta diff (B-A): {comparison.cost_delta_diff:+.6f}")
    con.print(f"  Differing decisions:  {len(comparison.differing_decisions)}")
    con.print()

    if comparison.differing_decisions:
        table = Table(title="Differing Decisions")
        table.add_column("Task Type", style="cyan")
        table.add_column("Requested", style="dim")
        table.add_column("Policy A →", style="green")
        table.add_column("Policy B →", style="yellow")

        for row_a, row_b in comparison.differing_decisions:
            table.add_row(
                row_a.expectation.task_type,
                row_a.expectation.requested_model,
                row_a.decision_model,
                row_b.decision_model,
            )

        con.print(table)
        con.print()


def render_calibration(buckets: list[CalibrationBucket], console: Console | None = None) -> None:
    con = console or Console()

    con.print("\n[bold underline]Confidence Calibration Curve[/bold underline]\n")

    table = Table(title="Calibration Buckets")
    table.add_column("Bin", justify="center")
    table.add_column("Avg Conf", justify="right")
    table.add_column("Accuracy", justify="right")
    table.add_column("Count", justify="right")
    table.add_column("Gap", justify="right")

    for b in buckets:
        gap = b.accuracy - b.avg_confidence
        gap_style = "green" if abs(gap) < 0.1 else "red"
        table.add_row(
            f"{b.bin_start:.1f}-{b.bin_end:.1f}",
            f"{b.avg_confidence:.3f}",
            f"{b.accuracy:.1%}",
            str(b.count),
            f"[{gap_style}]{gap:+.3f}[/{gap_style}]",
        )

    con.print(table)
    con.print()
    con.print("  Gap = accuracy - avg_confidence. Positive = underconfident, negative = overconfident.")
    con.print()


def render_e2e_report(report: E2EReport, console: Console | None = None) -> None:
    con = console or Console()

    con.print("\n[bold underline]End-to-End Paired Evaluation Report[/bold underline]\n")
    con.print(f"  Total prompts:        {report.total}")
    con.print(f"  Overridden:           {report.overridden_count}")
    con.print(f"  Avg quality delta:    {report.avg_quality_delta:+.3f}")
    con.print(f"  Quality degradations: {report.quality_degradation_count} (>0.05 worse)")
    con.print(f"  Total cost savings:   {report.total_cost_savings:+.6f}")
    con.print()

    overridden = [r for r in report.results if r.was_overridden]
    if overridden:
        table = Table(title="Overridden Decisions")
        table.add_column("Task", style="cyan")
        table.add_column("Requested", style="dim")
        table.add_column("Routed", style="green")
        table.add_column("Req Score", justify="right")
        table.add_column("Route Score", justify="right")
        table.add_column("Δ Quality", justify="right")

        for r in overridden:
            delta_style = "green" if r.quality_delta >= -0.05 else "red"
            table.add_row(
                r.task_type,
                r.requested_model,
                r.routed_model,
                f"{r.requested_score:.3f}",
                f"{r.routed_score:.3f}",
                f"[{delta_style}]{r.quality_delta:+.3f}[/{delta_style}]",
            )

        con.print(table)
        con.print()


def render_pareto(points: list[ParetoPoint], console: Console | None = None) -> None:
    con = console or Console()

    con.print("\n[bold underline]Cost-Quality Pareto Frontier[/bold underline]\n")

    table = Table(title="Model Points")
    table.add_column("Model", style="cyan")
    table.add_column("Avg Quality", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Frontier", justify="center")

    for p in sorted(points, key=lambda p: p.avg_quality, reverse=True):
        style = "bold green" if p.is_frontier else "dim"
        table.add_row(
            f"[{style}]{p.model}[/{style}]",
            f"{p.avg_quality:.3f}",
            f"{p.avg_cost:.6f}",
            "✓" if p.is_frontier else "",
        )

    con.print(table)
    con.print()
