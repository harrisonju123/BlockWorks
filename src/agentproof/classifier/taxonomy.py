"""Task classification types and result models.

Defines the boundary between the classifier and the pipeline.
The ClassifierInput is what the callback provides; the
ClassificationResult is what gets merged into the LLMEvent.
"""

from pydantic import BaseModel

from agentproof.types import TaskType


class ClassificationResult(BaseModel):
    task_type: TaskType
    confidence: float
    signals: list[str]


class ClassifierInput(BaseModel):
    """Structural metadata extracted from the LLM call before hashing.

    The classifier never sees raw prompt content — only structural
    signals that indicate what type of task this is.
    """

    system_prompt_hash: str | None
    has_tools: bool
    tool_count: int
    has_json_schema: bool
    has_code_fence_in_system: bool
    prompt_token_count: int
    completion_token_count: int
    token_ratio: float
    model: str
    system_prompt_keywords: list[str]
    user_prompt_keywords: list[str] = []
    output_format_hint: str | None
