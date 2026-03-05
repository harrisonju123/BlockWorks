"""blockthrough evaluate — run the classifier evaluation harness."""

from pathlib import Path

import typer

from blockthrough.classifier.evaluator import run


def evaluate(
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
) -> None:
    """Evaluate the classifier against a labeled dataset."""
    dataset_path = Path(dataset) if dataset else None
    model_name = None if rules or not model else model
    result = run(dataset_path, model=model_name)
    raise typer.Exit(code=0 if result.accuracy >= 0.75 else 1)
