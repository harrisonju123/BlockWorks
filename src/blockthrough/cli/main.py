"""Blockthrough CLI entry point."""

import typer

from blockthrough.cli.commands.evaluate import evaluate, evaluate_app
from blockthrough.cli.commands.stats import stats
from blockthrough.cli.commands.waste_report import waste_report

app = typer.Typer(
    name="blockthrough",
    help="AI agent observability, benchmarking, and attestation.",
    no_args_is_help=True,
)

app.command()(stats)
app.command()(evaluate)
app.command(name="waste-report")(waste_report)
app.add_typer(evaluate_app, name="eval")


if __name__ == "__main__":
    app()
