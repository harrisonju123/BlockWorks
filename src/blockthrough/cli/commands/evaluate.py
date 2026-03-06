"""blockthrough evaluate — classifier, routing, and e2e evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from blockthrough.classifier.evaluator import (
    calibration_curve,
    compare,
    evaluate as eval_rules,
    evaluate_llm,
    load_dataset,
    print_report,
    run,
)
from blockthrough.eval.report import (
    render_calibration,
    render_comparison_report,
    render_simulation_report,
)

evaluate_app = typer.Typer(
    name="evaluate",
    help="Evaluation harness for classifier, routing, and e2e.",
    no_args_is_help=True,
)


@evaluate_app.command()
def classifier(
    dataset: str = typer.Option(
        "",
        help="Path to a JSONL dataset file. Defaults to the built-in synthetic dataset.",
    ),
    model: str = typer.Option(
        "",
        help="LLM model to use for classification (e.g. google.gemma-3-27b-it). Empty = rules-based.",
    ),
    rules: bool = typer.Option(
        False,
        "--rules",
        help="Force rules-based classifier (baseline). Overrides --model.",
    ),
    do_compare: bool = typer.Option(
        False,
        "--compare",
        help="Run both rules and LLM classifier, then compare head-to-head.",
    ),
    do_calibration: bool = typer.Option(
        False,
        "--calibration",
        help="Show confidence calibration curve.",
    ),
    buckets: int = typer.Option(
        10,
        help="Number of buckets for calibration curve.",
    ),
) -> None:
    """Evaluate the classifier against a labeled dataset."""
    dataset_path = Path(dataset) if dataset else None

    if do_compare:
        if not model:
            typer.echo("--compare requires --model <name> for the LLM classifier.")
            raise typer.Exit(code=1)
        import asyncio

        examples = load_dataset(dataset_path)
        rules_result = eval_rules(examples)
        llm_result = asyncio.run(evaluate_llm(examples, model=model))

        print_report(rules_result, label="rules")
        print_report(llm_result, label=model)

        report = compare(rules_result, llm_result)
        from rich.console import Console

        con = Console()
        con.print(f"\n[bold underline]Head-to-Head Comparison[/bold underline]\n")
        con.print(f"  Rules accuracy:  {report.a_accuracy:.1%}")
        con.print(f"  LLM accuracy:    {report.b_accuracy:.1%}")
        con.print(f"  Agreement rate:  {report.agreement_rate:.1%}")
        con.print(f"  Disagreements:   {len(report.disagreements)}")
        con.print()
        for tt, winner in sorted(report.per_task_winner.items()):
            label = {"a": "rules", "b": model, "tie": "tie"}[winner]
            con.print(f"  {tt}: {label}")
        raise typer.Exit(code=0)

    model_name = None if rules or not model else model
    result = run(dataset_path, model=model_name)

    if do_calibration:
        buckets_data = calibration_curve(result, n_buckets=buckets)
        render_calibration(buckets_data)

    raise typer.Exit(code=0 if result.accuracy >= 0.75 else 1)


@evaluate_app.command()
def routing(
    policy: str = typer.Option(
        "",
        help="Path to a routing policy YAML/JSON file. Omit to use bootstrap_policy defaults.",
    ),
    expectations: str = typer.Option(
        "",
        help="Path to routing expectations JSONL. Defaults to built-in fixture.",
    ),
    policy_b: str = typer.Option(
        "",
        "--policy-b",
        help="Second policy file for comparison mode.",
    ),
) -> None:
    """Simulate routing policy against labeled expectations."""
    from blockthrough.eval.routing_eval import compare_policies, load_expectations, simulate_policy, simulate_with_defaults
    from blockthrough.routing.router import FitnessCache, generate_synthetic_fitness
    from blockthrough.routing.types import RoutingPolicy

    exp_path = Path(expectations) if expectations else None
    exps = load_expectations(exp_path)

    # No explicit policy → use bootstrap defaults
    if not policy and not policy_b:
        report = simulate_with_defaults(exps)
        render_simulation_report(report)
        raise typer.Exit(code=0)

    if not policy:
        typer.echo("--policy is required when using --policy-b for comparison.")
        raise typer.Exit(code=1)

    # Load explicit policy
    policy_path = Path(policy)
    policy_data = json.loads(policy_path.read_text())
    routing_policy = RoutingPolicy.model_validate(policy_data)

    # Build fitness cache from synthetic data
    cache = FitnessCache(ttl_s=300)
    cache.update(generate_synthetic_fitness())

    if policy_b:
        policy_b_data = json.loads(Path(policy_b).read_text())
        routing_policy_b = RoutingPolicy.model_validate(policy_b_data)
        comparison = compare_policies(exps, routing_policy, routing_policy_b, cache)
        render_comparison_report(comparison)
    else:
        report = simulate_policy(exps, routing_policy, cache)
        render_simulation_report(report)

    raise typer.Exit(code=0)


# Backward-compatible top-level command for `blockthrough evaluate`
def evaluate(
    dataset: str = typer.Option(
        "",
        help="Path to a JSONL dataset file. Defaults to the built-in synthetic dataset.",
    ),
    model: str = typer.Option(
        "",
        help="LLM model to use for classification. Empty = rules-based.",
    ),
    rules: bool = typer.Option(
        False,
        "--rules",
        help="Force rules-based classifier. Overrides --model.",
    ),
) -> None:
    """Evaluate the classifier against a labeled dataset (backward-compatible)."""
    dataset_path = Path(dataset) if dataset else None
    model_name = None if rules or not model else model
    result = run(dataset_path, model=model_name)
    raise typer.Exit(code=0 if result.accuracy >= 0.75 else 1)
