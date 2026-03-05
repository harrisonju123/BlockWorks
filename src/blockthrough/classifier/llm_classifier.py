"""LLM-based task classifier.

Sends extracted structural signals (never raw content) to a budget LLM
for classification. Falls back to rules-based classifier on any failure.
"""

from __future__ import annotations

import asyncio
import logging

import litellm

from blockthrough.classifier.taxonomy import ClassificationResult, ClassifierInput
from blockthrough.types import TaskType

logger = logging.getLogger(__name__)

_VALID_TYPES = {t.value for t in TaskType if t != TaskType.UNKNOWN}

_SYSTEM_PROMPT = (
    "You are a task classifier. Given structural signals from an LLM request, "
    "classify it into exactly one category.\n\n"
    "Categories: code_generation, code_review, classification, summarization, "
    "extraction, reasoning, conversation, tool_selection\n\n"
    "Respond with ONLY the category name, nothing else."
)


def _build_prompt(inp: ClassifierInput) -> str:
    """Build a concise signal summary from ClassifierInput.

    Gives the LLM all structural context without raw prompt content.
    Typically ~100-200 tokens.
    """
    lines = ["Signals:"]

    # Tool signals
    if inp.has_tools:
        tool_desc = f"{inp.tool_count} tools available"
        if inp.has_tool_calls:
            tool_desc += ", model used tools"
        lines.append(f"- Tools: {tool_desc}")
    elif inp.has_tool_calls:
        lines.append("- Tools: model used tools (no tool array)")

    # Structural signals
    lines.append(f"- Code fences in system prompt: {'yes' if inp.has_code_fence_in_system else 'no'}")
    lines.append(f"- JSON schema in output: {'yes' if inp.has_json_schema else 'no'}")

    # Token ratio
    ratio_desc = "balanced"
    if inp.token_ratio < 0.1:
        ratio_desc = "very short output"
    elif inp.token_ratio < 0.5:
        ratio_desc = "short output"
    elif inp.token_ratio > 3.0:
        ratio_desc = "very long output"
    elif inp.token_ratio > 1.5:
        ratio_desc = "long output"
    lines.append(f"- Token ratio: {inp.token_ratio:.1f} ({ratio_desc})")

    # Keywords (the classifier-relevant terms, not raw content)
    if inp.system_prompt_keywords:
        lines.append(f"- System keywords: {', '.join(inp.system_prompt_keywords[:10])}")
    if inp.user_prompt_keywords:
        lines.append(f"- User keywords: {', '.join(inp.user_prompt_keywords[:10])}")

    # Output format
    if inp.output_format_hint:
        lines.append(f"- Output format hint: {inp.output_format_hint}")

    return "\n".join(lines)


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
) -> ClassificationResult:
    """Classify using a budget LLM call via litellm.

    Raises on failure so the caller can fall back to rules-based.
    """
    prompt = _build_prompt(task_input)

    response = await asyncio.wait_for(
        litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=20,
            temperature=0.0,
        ),
        timeout=timeout_s,
    )

    raw = response.choices[0].message.content.strip()
    task_type, confidence = _parse_response(raw)

    return ClassificationResult(
        task_type=task_type,
        confidence=confidence,
        signals=[f"llm_classifier:{model}", f"llm_raw:{raw[:50]}"],
    )
