"""Routing policy simulation and comparison.

Runs resolve() offline against a set of labeled expectations to measure
how well a routing policy behaves: override rate, cost savings, quality risk.
"""

from __future__ import annotations

from pathlib import Path

from blockthrough.eval.types import (
    ExpectedBehavior,
    PolicyComparison,
    RoutingExpectation,
    SimulationReport,
    SimulationRow,
    TaskBreakdown,
    model_cost,
)
from blockthrough.routing.router import FitnessCache, generate_synthetic_fitness, resolve
from blockthrough.routing.types import RoutingPolicy

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DEFAULT_EXPECTATIONS = _FIXTURES_DIR / "routing_expectations.jsonl"


def load_expectations(path: Path | None = None) -> list[RoutingExpectation]:
    """Load routing expectations from a JSONL file."""
    exp_path = path or _DEFAULT_EXPECTATIONS
    exps: list[RoutingExpectation] = []
    with exp_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                exps.append(RoutingExpectation.model_validate_json(line))
    return exps


def _build_quality_index(fitness_cache: FitnessCache) -> dict[tuple[str, str], float]:
    """Pre-build (model, task_type) -> quality index from the fitness cache."""
    index: dict[tuple[str, str], float] = {}
    for entry in fitness_cache.get_all_entries():
        index[(entry.model, entry.task_type)] = entry.avg_quality
    return index


def simulate_policy(
    expectations: list[RoutingExpectation],
    policy: RoutingPolicy,
    fitness_cache: FitnessCache,
) -> SimulationReport:
    """Run resolve() for each expectation and compare against expected behavior."""
    quality_index = _build_quality_index(fitness_cache)
    rows: list[SimulationRow] = []
    behavior_matches = 0
    override_count = 0
    total_cost_delta = 0.0
    quality_risk_count = 0
    # Accumulate counts as plain lists: [override, passthrough, total]
    per_task_counts: dict[str, list[int]] = {}

    for exp in expectations:
        decision = resolve(
            exp.task_type,
            exp.requested_model,
            fitness_cache,
            policy,
            has_tool_use=exp.has_tool_use,
            allowed_models=exp.allowed_models,
        )

        # Check behavior match
        if exp.expected_behavior == ExpectedBehavior.OVERRIDE:
            behavior_ok = decision.was_overridden
        else:
            behavior_ok = not decision.was_overridden

        # Check exact model match if specified
        model_ok: bool | None = None
        if exp.expected_model is not None:
            model_ok = decision.selected_model == exp.expected_model

        # Cost delta: selected - requested (negative = savings)
        cost_delta = model_cost(decision.selected_model) - model_cost(exp.requested_model)

        # Quality risk: override where selected model quality is notably worse
        if decision.was_overridden:
            selected_q = quality_index.get((decision.selected_model, exp.task_type))
            requested_q = quality_index.get((exp.requested_model, exp.task_type))
            if selected_q is not None and requested_q is not None:
                if requested_q - selected_q > 0.1:
                    quality_risk_count += 1

        if behavior_ok:
            behavior_matches += 1
        if decision.was_overridden:
            override_count += 1
        total_cost_delta += cost_delta

        # Per-task breakdown
        counts = per_task_counts.setdefault(exp.task_type, [0, 0, 0])
        counts[2] += 1
        if decision.was_overridden:
            counts[0] += 1
        else:
            counts[1] += 1

        rows.append(
            SimulationRow(
                expectation=exp,
                decision_model=decision.selected_model,
                decision_reason=decision.reason,
                decision_was_overridden=decision.was_overridden,
                behavior_match=behavior_ok,
                model_match=model_ok,
                cost_delta=cost_delta,
            )
        )

    total = len(expectations)
    per_task = {
        tt: TaskBreakdown(override_count=c[0], passthrough_count=c[1], total=c[2])
        for tt, c in per_task_counts.items()
    }
    return SimulationReport(
        total=total,
        behavior_matches=behavior_matches,
        behavior_accuracy=behavior_matches / total if total > 0 else 0.0,
        override_rate=override_count / total if total > 0 else 0.0,
        avg_cost_delta=total_cost_delta / total if total > 0 else 0.0,
        quality_risk_count=quality_risk_count,
        per_task_breakdown=per_task,
        rows=rows,
    )


def compare_policies(
    expectations: list[RoutingExpectation],
    policy_a: RoutingPolicy,
    policy_b: RoutingPolicy,
    fitness_cache: FitnessCache,
) -> PolicyComparison:
    """Run simulate_policy for two policies and diff the results."""
    report_a = simulate_policy(expectations, policy_a, fitness_cache)
    report_b = simulate_policy(expectations, policy_b, fitness_cache)

    agreements = 0
    differing: list[tuple[SimulationRow, SimulationRow]] = []

    for row_a, row_b in zip(report_a.rows, report_b.rows):
        if row_a.decision_model == row_b.decision_model:
            agreements += 1
        else:
            differing.append((row_a, row_b))

    total = len(expectations)
    return PolicyComparison(
        policy_a_report=report_a,
        policy_b_report=report_b,
        behavior_agreement_rate=agreements / total if total > 0 else 0.0,
        cost_delta_diff=report_b.avg_cost_delta - report_a.avg_cost_delta,
        differing_decisions=differing,
    )


def simulate_with_defaults(
    expectations: list[RoutingExpectation],
    fitness_cache: FitnessCache | None = None,
) -> SimulationReport:
    """Run simulation using bootstrap_policy + synthetic fitness.

    Convenience wrapper that wires up the default policy and synthetic
    fitness data so callers don't need to construct them manually.
    """
    from blockthrough.routing.policy import bootstrap_policy, clear_bootstrap_cache

    if fitness_cache is None:
        fitness_cache = FitnessCache(ttl_s=300)
        fitness_cache.update(generate_synthetic_fitness())

    clear_bootstrap_cache()
    policy = bootstrap_policy(fitness_cache=fitness_cache)
    return simulate_policy(expectations, policy, fitness_cache)
