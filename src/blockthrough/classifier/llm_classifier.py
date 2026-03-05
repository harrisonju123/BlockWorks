"""LLM-based task classifier.

Sends extracted structural signals (never raw content) to a budget LLM
for classification. Falls back to rules-based classifier on any failure.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from blockthrough.classifier.taxonomy import ClassificationResult, ClassifierInput
from blockthrough.types import TaskType

logger = logging.getLogger(__name__)

_VALID_TYPES = {t.value for t in TaskType if t != TaskType.UNKNOWN}

_SYSTEM_PROMPT = (
    "Given structural signals from an LLM request, determine the primary "
    "task the user is asking the model to perform.\n\n"
    "Possible task types:\n"
    "- code_generation: writing, implementing, or refactoring code\n"
    "- code_review: reviewing diffs, PRs, auditing code quality\n"
    "- classification: labeling, categorizing, or detecting sentiment\n"
    "- summarization: condensing or summarizing text\n"
    "- extraction: parsing or pulling structured data from text\n"
    "- reasoning: explaining, analyzing, or step-by-step thinking\n"
    "- conversation: casual chat or dialogue\n"
    "- tool_selection: choosing or invoking tools/functions\n\n"
    "Respond with ONLY the task type name, nothing else."
)


def _build_prompt(inp: ClassifierInput) -> str:
    """Extract the last user message for classification.

    Falls back to a minimal signal summary if no user message is available.
    """
    if inp.last_user_message:
        # Truncate to ~500 chars to keep Gemma's input small
        msg = inp.last_user_message[:500]
        return f"User message:\n{msg}"

    # Fallback: no user message available (shouldn't happen in practice)
    return "No user message available."


def _parse_response(raw: str) -> tuple[TaskType, float]:
    """Parse the LLM response into a TaskType and confidence.

    Returns (TaskType.UNKNOWN, 0.0) if unparseable.
    """
    cleaned = raw.strip().lower().replace("-", "_").replace(" ", "_")

    # Strip common prefixes the model might add
    for prefix in ("category:", "type:", "task:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    if cleaned in _VALID_TYPES:
        # Exact match — high confidence since the model returned a clean label
        return TaskType(cleaned), 0.85

    # Fuzzy: check if any valid type is a substring (model added preamble)
    for valid in _VALID_TYPES:
        if valid in cleaned:
            return TaskType(valid), 0.7

    return TaskType.UNKNOWN, 0.0


async def llm_classify(
    task_input: ClassifierInput,
    model: str = "google.gemma-3-27b-it",
    timeout_s: float = 2.0,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> ClassificationResult:
    """Classify via the upstream LiteLLM proxy using httpx.

    Uses the same httpx path as regular chat messages to avoid litellm
    client-side provider routing that misroutes prefixed model names.
    Raises on failure so the caller can fall back to rules-based.
    """
    prompt = _build_prompt(task_input)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 20,
        "temperature": 0.0,
    }
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if client is None:
        raise RuntimeError("llm_classify requires an httpx client")

    resp = await asyncio.wait_for(
        client.post("/v1/chat/completions", json=body, headers=headers),
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()

    raw = data["choices"][0]["message"]["content"].strip()
    task_type, confidence = _parse_response(raw)

    return ClassificationResult(
        task_type=task_type,
        confidence=confidence,
        signals=[f"llm_classifier:{model}", f"llm_raw:{raw[:50]}"],
    )
