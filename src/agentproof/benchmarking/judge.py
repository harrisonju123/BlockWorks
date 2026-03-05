"""LLM-as-judge evaluation engine.

Uses Sonnet by default to score LLM outputs against
task-specific rubrics. The judge never sees raw user content -- only
the prompt/completion pair and the rubric. Scoring is fully async
and runs in the background benchmark worker.
"""

from __future__ import annotations

import json
import logging

import litellm

from agentproof.benchmarking.types import Rubric, RubricCriterion
from agentproof.types import TaskType

logger = logging.getLogger(__name__)

# Rubric version tracks scoring methodology changes.
# Bump when rubric criteria or weights change so that attested
# benchmark claims can be tied to a known evaluation methodology.
RUBRIC_VERSION = "1.0"

_RUBRICS: dict[TaskType, Rubric] = {
    TaskType.CODE_REVIEW: Rubric(
        task_type=TaskType.CODE_REVIEW,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="bug_detection",
                weight=0.4,
                prompt=(
                    "Does the review identify real bugs or issues in the code? "
                    "Score 0.0 (misses everything) to 1.0 (catches all issues)."
                ),
            ),
            RubricCriterion(
                name="actionability",
                weight=0.35,
                prompt=(
                    "Are the review comments actionable with clear suggestions for improvement? "
                    "Score 0.0 (vague/unhelpful) to 1.0 (specific and actionable)."
                ),
            ),
            RubricCriterion(
                name="completeness",
                weight=0.25,
                prompt=(
                    "Does the review cover all relevant aspects of the code change? "
                    "Score 0.0 (very partial) to 1.0 (comprehensive coverage)."
                ),
            ),
        ],
    ),
    TaskType.CODE_GENERATION: Rubric(
        task_type=TaskType.CODE_GENERATION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="correctness",
                weight=0.5,
                prompt=(
                    "Does the generated code solve the stated problem? "
                    "Score 0.0 (completely wrong) to 1.0 (fully correct)."
                ),
            ),
            RubricCriterion(
                name="style",
                weight=0.3,
                prompt=(
                    "Is the code idiomatic, well-structured, and readable? "
                    "Score 0.0 (unreadable) to 1.0 (exemplary)."
                ),
            ),
            RubricCriterion(
                name="completeness",
                weight=0.2,
                prompt=(
                    "Does the code handle edge cases and provide a complete solution? "
                    "Score 0.0 (incomplete) to 1.0 (fully complete)."
                ),
            ),
        ],
    ),
    TaskType.CLASSIFICATION: Rubric(
        task_type=TaskType.CLASSIFICATION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="accuracy",
                weight=1.0,
                prompt=(
                    "Does the classification match the expected output? "
                    "Score 1.0 for exact match, 0.5 for partially correct, 0.0 for wrong."
                ),
            ),
        ],
    ),
    TaskType.SUMMARIZATION: Rubric(
        task_type=TaskType.SUMMARIZATION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="completeness",
                weight=0.4,
                prompt=(
                    "Does the summary capture all key points from the original? "
                    "Score 0.0 (misses everything) to 1.0 (all key points present)."
                ),
            ),
            RubricCriterion(
                name="conciseness",
                weight=0.3,
                prompt=(
                    "Is the summary appropriately concise without unnecessary detail? "
                    "Score 0.0 (extremely verbose) to 1.0 (optimally concise)."
                ),
            ),
            RubricCriterion(
                name="accuracy",
                weight=0.3,
                prompt=(
                    "Is the summary factually accurate with no hallucinated content? "
                    "Score 0.0 (factually wrong) to 1.0 (perfectly accurate)."
                ),
            ),
        ],
    ),
    TaskType.EXTRACTION: Rubric(
        task_type=TaskType.EXTRACTION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="precision",
                weight=0.5,
                prompt=(
                    "Are all extracted items actually present in the source? "
                    "Score 0.0 (all hallucinated) to 1.0 (all correct)."
                ),
            ),
            RubricCriterion(
                name="recall",
                weight=0.5,
                prompt=(
                    "Were all relevant items from the source extracted? "
                    "Score 0.0 (missed everything) to 1.0 (nothing missed)."
                ),
            ),
        ],
    ),
    TaskType.REASONING: Rubric(
        task_type=TaskType.REASONING,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="logical_soundness",
                weight=0.6,
                prompt=(
                    "Is the reasoning logically sound with valid inferences? "
                    "Score 0.0 (logically broken) to 1.0 (flawless logic)."
                ),
            ),
            RubricCriterion(
                name="step_coverage",
                weight=0.4,
                prompt=(
                    "Does the response cover all necessary reasoning steps? "
                    "Score 0.0 (skips critical steps) to 1.0 (all steps present)."
                ),
            ),
        ],
    ),
    TaskType.CONVERSATION: Rubric(
        task_type=TaskType.CONVERSATION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="helpfulness",
                weight=0.5,
                prompt=(
                    "Is the response helpful and addresses the user's need? "
                    "Score 0.0 (unhelpful) to 1.0 (maximally helpful)."
                ),
            ),
            RubricCriterion(
                name="relevance",
                weight=0.5,
                prompt=(
                    "Is the response relevant and on-topic? "
                    "Score 0.0 (completely off-topic) to 1.0 (perfectly relevant)."
                ),
            ),
        ],
    ),
    TaskType.TOOL_SELECTION: Rubric(
        task_type=TaskType.TOOL_SELECTION,
        version=RUBRIC_VERSION,
        criteria=[
            RubricCriterion(
                name="correct_tool",
                weight=1.0,
                prompt=(
                    "Did the model select the correct tool(s) for the task? "
                    "Score 1.0 for correct selection, 0.5 for partially correct, "
                    "0.0 for wrong tool."
                ),
            ),
        ],
    ),
}


def get_rubric(task_type: TaskType) -> Rubric | None:
    """Look up the scoring rubric for a task type. Returns None for UNKNOWN."""
    return _RUBRICS.get(task_type)


def _build_judge_prompt(
    original_prompt: str,
    original_completion: str,
    benchmark_completion: str,
    rubric: Rubric,
) -> str:
    """Construct the system+user prompt sent to the judge model.

    The judge sees both outputs and scores the benchmark completion
    against each rubric criterion. It returns structured JSON so we
    can compute the weighted quality score deterministically.
    """
    criteria_block = "\n".join(
        f"- {c.name} (weight {c.weight}): {c.prompt}" for c in rubric.criteria
    )
    criteria_names = [c.name for c in rubric.criteria]

    return f"""You are an expert evaluator. Score the BENCHMARK completion on its own merits using the rubric below.

A REFERENCE completion from a stronger model is provided for calibration — it shows what a high-quality response looks like. Do NOT penalize the benchmark for taking a different approach, structure, or level of detail. Only penalize for genuinely lower quality: missed issues, incorrect information, or incomplete coverage.

TASK TYPE: {rubric.task_type.value}

RUBRIC:
{criteria_block}

ORIGINAL PROMPT:
{original_prompt}

REFERENCE COMPLETION (calibration only):
{original_completion}

BENCHMARK COMPLETION (score this):
{benchmark_completion}

Score each criterion based on the absolute quality of the BENCHMARK COMPLETION, not its similarity to the reference. Each score must be a float between 0.0 and 1.0.
Return ONLY a JSON object.
Example format: {json.dumps({name: 0.85 for name in criteria_names})}

JSON:"""


def _parse_judge_response(response_text: str, rubric: Rubric) -> dict[str, float]:
    """Extract per-criterion scores from the judge's JSON response.

    Falls back to a default low score if parsing fails -- we never want
    a judge formatting error to crash the benchmark pipeline.
    """
    try:
        # Strip markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            # Handle ```json prefix
            if text.startswith("json"):
                text = text[4:].strip()

        scores = json.loads(text)
        result: dict[str, float] = {}
        for criterion in rubric.criteria:
            raw = scores.get(criterion.name, 0.0)
            result[criterion.name] = max(0.0, min(1.0, float(raw)))
        return result
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Failed to parse judge response, defaulting to 0.0: %s", response_text[:200])
        return {c.name: 0.0 for c in rubric.criteria}


def compute_weighted_score(scores: dict[str, float], rubric: Rubric) -> float:
    """Compute the weighted average quality score from per-criterion scores."""
    total = 0.0
    for criterion in rubric.criteria:
        total += criterion.weight * scores.get(criterion.name, 0.0)
    return max(0.0, min(1.0, total))


async def evaluate(
    original_prompt: str,
    original_completion: str,
    benchmark_completion: str,
    task_type: TaskType,
    judge_model: str = "claude-sonnet-4-6",
    api_base: str | None = None,
) -> tuple[float, str]:
    """Score a benchmark completion against the original using the LLM-as-judge.

    Returns (quality_score, rubric_version). The quality_score is a weighted
    sum across all rubric criteria, clamped to [0.0, 1.0].

    Raises ValueError if no rubric exists for the task type (e.g. UNKNOWN).
    """
    rubric = get_rubric(task_type)
    if rubric is None:
        raise ValueError(f"No rubric defined for task type: {task_type.value}")

    prompt = _build_judge_prompt(
        original_prompt, original_completion, benchmark_completion, rubric
    )

    kwargs: dict = {
        "model": judge_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 256,
    }
    if api_base:
        kwargs["api_base"] = api_base

    response = await litellm.acompletion(**kwargs)

    response_text = response.choices[0].message.content or ""
    scores = _parse_judge_response(response_text, rubric)
    quality_score = compute_weighted_score(scores, rubric)

    return quality_score, rubric.version
