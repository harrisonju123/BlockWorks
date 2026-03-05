"""Rules-based task classifier.

Ships in Week 1-2. Uses structural signals from the prompt to
classify task type. Deterministic, zero external dependencies,
debuggable via the signals list.
"""

import re

from agentproof.classifier.taxonomy import ClassificationResult, ClassifierInput
from agentproof.types import TaskType

TASK_KEYWORDS: dict[TaskType, list[str]] = {
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
        "write code", "write a function", "write a class",
        "implement a", "implement the", "implement this",
        "generate code", "create a script", "code this",
        "build a function", "build a class", "refactor this",
        "write a program", "write a module",
    ],
    TaskType.CODE_REVIEW: [
        "code review", "review code", "review this code", "review this diff",
        "review this pr", "find bugs", "find issues", "audit", "critique",
        "code quality", "pull request", "pr review", "diff review",
        "security review", "peer review",
    ],
    TaskType.REASONING: [
        "explain why", "explain how", "reason about", "analyze this",
        "think step", "chain of thought", "let's think",
        "reason through", "walk me through",
    ],
    TaskType.CONVERSATION: [
        "chat", "converse", "have a conversation", "dialogue",
        "just talk", "casual conversation",
    ],
    TaskType.TOOL_SELECTION: [
        "select tool", "pick tool", "choose function", "use tool",
    ],
}

# Precompile word-boundary regexes for single-word keywords so the hot-path
# classifier doesn't pay re.compile cost on every call.
_WORD_PATTERNS: dict[str, re.Pattern[str]] = {}
for _kw_list in TASK_KEYWORDS.values():
    for _kw in _kw_list:
        if " " not in _kw and _kw not in _WORD_PATTERNS:
            _WORD_PATTERNS[_kw] = re.compile(r"\b" + re.escape(_kw) + r"\b")


def _matches_keyword(text: str, keyword: str) -> bool:
    """Check whether *keyword* appears in *text* as a whole word.

    Multi-word phrases use plain substring matching (they're unlikely to
    cause false positives). Single-word keywords use a precompiled
    word-boundary regex to avoid hits like "class" inside "classify".
    """
    if " " in keyword:
        return keyword in text
    return bool(_WORD_PATTERNS[keyword].search(text))


def extract_keywords(text: str) -> list[str]:
    """Scan text for classifier-relevant keywords from TASK_KEYWORDS."""
    lower = text.lower()
    found: list[str] = []
    for keywords in TASK_KEYWORDS.values():
        for kw in keywords:
            if _matches_keyword(lower, kw):
                found.append(kw)
    return found


def compute_token_ratio(prompt_tokens: int, completion_tokens: int) -> float:
    """Safe division for token ratio used by ClassifierInput."""
    return completion_tokens / prompt_tokens if prompt_tokens > 0 else 0.0


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

    # Signal: keyword matching from system prompt (low weight — system
    # prompts for coding assistants are full of generic code terms)
    for task_type, keywords in TASK_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in task_input.system_prompt_keywords]
        if matched:
            signals.append(f"keywords_{task_type.value}:{','.join(matched)}")
            scores[task_type] += 0.15 * len(matched)

    # Signal: keyword matching from user prompt (stronger — direct intent)
    for task_type, keywords in TASK_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in task_input.user_prompt_keywords]
        if matched:
            signals.append(f"user_keywords_{task_type.value}:{','.join(matched)}")
            scores[task_type] += 0.5 * len(matched)

    # Disambiguation: review keywords + code fences → code review, not generation
    review_keywords_present = scores[TaskType.CODE_REVIEW] > 0
    if review_keywords_present and task_input.has_code_fence_in_system:
        signals.append("review_keywords_with_code_fence")
        scores[TaskType.CODE_REVIEW] += 0.3
        scores[TaskType.CODE_GENERATION] -= 0.2

    # Signal: output format hint
    if task_input.output_format_hint:
        hint = task_input.output_format_hint.lower()
        if hint == "json":
            signals.append("output_hint_json")
            scores[TaskType.EXTRACTION] += 0.2
            scores[TaskType.CLASSIFICATION] += 0.2
        elif hint == "code":
            if review_keywords_present:
                signals.append("output_hint_code_as_review")
                scores[TaskType.CODE_REVIEW] += 0.2
            else:
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

    # Normalize confidence to 0-1 range (cap at 1.0).
    # Round to 10 decimal places to avoid IEEE 754 float artifacts
    # (e.g. 0.3/1.5 producing 0.19999999999999998 instead of 0.2).
    confidence = round(min(best_score / 1.5, 1.0), 10)

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
