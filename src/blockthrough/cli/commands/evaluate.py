"""blockthrough evaluate — run the classifier evaluation harness."""

from pathlib import Path

import typer

from blockthrough.classifier.evaluator import run


def evaluate(
    dataset: str = typer.Option(
        "",
        help="Path to a JSONL dataset file. Defaults to the built-in synthetic dataset.",
    ),
) -> None:
    """Evaluate the rules-based classifier against a labeled dataset."""
    dataset_path = Path(dataset) if dataset else None
    result = run(dataset_path)
    raise typer.Exit(code=0 if result.accuracy >= 0.75 else 1)
