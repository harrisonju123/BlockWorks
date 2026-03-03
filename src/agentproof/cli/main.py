"""AgentProof CLI entry point."""

import typer

from agentproof.cli.commands.stats import stats

app = typer.Typer(
    name="agentproof",
    help="AI agent observability, benchmarking, and attestation.",
    no_args_is_help=True,
)

app.command()(stats)


if __name__ == "__main__":
    app()
