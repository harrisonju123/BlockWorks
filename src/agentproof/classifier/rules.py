"""Rules-based task classifier.

Ships in Week 1-2. Uses structural signals from the prompt to
classify task type. Deterministic, zero external dependencies,
debuggable via the signals list.
"""

from agentproof.classifier.taxonomy import ClassificationResult, ClassifierInput
from agentproof.types import TaskType

# Keywords that strongly signal a particular task type
_TASK_KEYWORDS: dict[TaskType, list[str]] = {
    TaskType.CLASSIFICATION: [
        "classify", "categorize", "label", "sentiment", "detect", "identify",
    ],
    TaskType.SUMMARIZATION: [
        "summarize", "summary", "tldr", "condense", "brief",
    ],
    TaskType.EXTRACTION: [
        "extract", "parse", "pull out", "find all", "list the",
    ],
    TaskType.CODE_GENERATION: [
        "write code", "implement", "function", "class", "refactor",
        "generate code", "create a script",
    ],
    TaskType.REASONING: [
        "explain", "why", "reason", "analyze", "think step",
        "chain of thought", "let's think",
    ],
}


def classify(task_input: ClassifierInput) -> ClassificationResult:
    """Classify a task based on structural prompt signals."""
    signals: list[str] = []
    scores: dict[TaskType, float] = {t: 0.0 for t in TaskType}

    # Signal: tool array present → likely tool selection
    if task_input.has_tools:
        signals.append("tool_array_present")
        scores[TaskType.TOOL_SELECTION] += 0.6
        if task_input.tool_count > 3:
            signals.append("many_tools")
            scores[TaskType.TOOL_SELECTION] += 0.2

    # Signal: code fences in system prompt → likely code generation
    if task_input.has_code_fence_in_system:
        signals.append("code_fence_in_system")
        scores[TaskType.CODE_GENERATION] += 0.4

    # Signal: JSON schema output → likely classification or extraction
    if task_input.has_json_schema:
        signals.append("json_schema_output")
        scores[TaskType.CLASSIFICATION] += 0.3
        scores[TaskType.EXTRACTION] += 0.3

    # Signal: low token ratio (short output) → likely classification
    if task_input.token_ratio < 0.1 and task_input.completion_token_count < 50:
        signals.append("low_token_ratio")
        scores[TaskType.CLASSIFICATION] += 0.3

    # Signal: high token ratio (long output) → likely code gen or reasoning
    if task_input.token_ratio > 2.0:
        signals.append("high_token_ratio")
        scores[TaskType.CODE_GENERATION] += 0.2
        scores[TaskType.REASONING] += 0.2

    # Signal: keyword matching from system prompt
    for task_type, keywords in _TASK_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in task_input.system_prompt_keywords]
        if matched:
            signals.append(f"keywords_{task_type.value}:{','.join(matched)}")
            scores[task_type] += 0.4 * len(matched)

    # Signal: output format hint
    if task_input.output_format_hint:
        hint = task_input.output_format_hint.lower()
        if hint == "json":
            signals.append("output_hint_json")
            scores[TaskType.EXTRACTION] += 0.2
            scores[TaskType.CLASSIFICATION] += 0.2
        elif hint == "code":
            signals.append("output_hint_code")
            scores[TaskType.CODE_GENERATION] += 0.4
        elif hint == "markdown":
            signals.append("output_hint_markdown")
            scores[TaskType.SUMMARIZATION] += 0.1
            scores[TaskType.REASONING] += 0.1

    # If no tools and no strong signals, check for conversation pattern
    if not task_input.has_tools and max(scores.values()) < 0.3:
        signals.append("no_strong_signals_conversation_fallback")
        scores[TaskType.CONVERSATION] += 0.3

    # Pick the highest-scoring type
    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]

    # Normalize confidence to 0-1 range (cap at 1.0)
    confidence = min(best_score / 1.5, 1.0)

    # If confidence is too low, fall back to UNKNOWN
    if confidence < 0.2:
        return ClassificationResult(
            task_type=TaskType.UNKNOWN,
            confidence=confidence,
            signals=signals,
        )

    return ClassificationResult(
        task_type=best_type,
        confidence=confidence,
        signals=signals,
    )
