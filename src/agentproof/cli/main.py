"""AgentProof CLI entry point."""

import typer

from agentproof.cli.commands.evaluate import evaluate
from agentproof.cli.commands.stats import stats
from agentproof.cli.commands.waste_report import waste_report

app = typer.Typer(
    name="agentproof",
    help="AI agent observability, benchmarking, and attestation.",
    no_args_is_help=True,
)

app.command()(stats)
app.command()(evaluate)
app.command(name="waste-report")(waste_report)


if __name__ == "__main__":
    app()
