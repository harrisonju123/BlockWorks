"""Eval set loader for benchmark evaluation.

Loads synthetic prompts from JSONL fixtures, deduplicates by content hash,
and converts to litellm message format for replay.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agentproof.pipeline.hasher import hash_content
from agentproof.types import TaskType

logger = logging.getLogger(__name__)

_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "classifier"
    / "fixtures"
    / "synthetic_prompts.jsonl"
)


@dataclass(frozen=True)
class EvalPrompt:
    """A single eval prompt with its metadata."""

    system_prompt: str
    user_prompt: str
    task_type: TaskType
    prompt_hash: str = field(repr=False)

    @staticmethod
    def from_jsonl(data: dict) -> EvalPrompt:
        return EvalPrompt(
            system_prompt=data["system_prompt"],
            user_prompt=data["user_prompt"],
            task_type=TaskType(data["expected_task_type"]),
            prompt_hash=hash_content(
                [
                    {"role": "system", "content": data["system_prompt"]},
                    {"role": "user", "content": data["user_prompt"]},
                ]
            ),
        )


def to_messages(prompt: EvalPrompt) -> list[dict]:
    """Convert an EvalPrompt to litellm message format."""
    return [
        {"role": "system", "content": prompt.system_prompt},
        {"role": "user", "content": prompt.user_prompt},
    ]


def load_eval_set(
    path: Path | None = None,
    task_types: set[TaskType] | None = None,
) -> list[EvalPrompt]:
    """Load eval prompts from JSONL, optionally filtering by task type.

    Deduplicates by prompt_hash so repeated entries are ignored.
    """
    path = path or _DEFAULT_FIXTURE
    seen: set[str] = set()
    prompts: list[EvalPrompt] = []

    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line %d in %s", lineno, path)
                continue

            try:
                ep = EvalPrompt.from_jsonl(data)
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping line %d: %s", lineno, exc)
                continue

            if task_types and ep.task_type not in task_types:
                continue

            if ep.prompt_hash in seen:
                continue
            seen.add(ep.prompt_hash)
            prompts.append(ep)

    logger.info(
        "Loaded %d eval prompts from %s (filtered to %s)",
        len(prompts),
        path.name,
        [t.value for t in task_types] if task_types else "all",
    )
    return prompts
