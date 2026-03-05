"""Consolidated model catalog — single source of truth for pricing, tiers, and downgrades.

Merges the former MODEL_COST_TIERS (waste.py) and MODEL_DOWNGRADE_MAP (budgets.py)
into one registry. Every module that needs model metadata should import from here.
"""

from __future__ import annotations

from dataclasses import dataclass

# Valid task type values for task_qualities validation.
# Kept in sync with TaskType enum — import-time validation below catches drift.
_VALID_TASK_TYPES: frozenset[str] = frozenset({
    "code_generation", "code_review", "classification", "summarization",
    "extraction", "reasoning", "conversation", "tool_selection",
})


@dataclass(frozen=True)
class ModelInfo:
    """Pricing, tier placement, and downgrade path for one model."""

    tier: int
    cost_per_1k_input: float
    cost_per_1k_output: float
    downgrade_to: str | None = None
    supports_tool_use: bool = True
    supports_thinking: bool = True
    # Per-task quality scores (0.0–1.0) for synthetic fitness generation.
    # Keys are TaskType values. Missing keys fall back to the tier default.
    # This lets the router differentiate models within the same tier —
    # e.g. GPT-5.2 scores high on reasoning, GPT-OSS scores lower.
    task_qualities: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        for task_key, _ in self.task_qualities:
            if task_key not in _VALID_TASK_TYPES:
                raise ValueError(
                    f"Unknown task type '{task_key}' in task_qualities. "
                    f"Valid: {sorted(_VALID_TASK_TYPES)}"
                )

    @property
    def avg_cost(self) -> float:
        return (self.cost_per_1k_input + self.cost_per_1k_output) / 2

    def quality_for_task(self, task_type: str, tier_default: float) -> float:
        """Return the quality score for a task type, falling back to tier default."""
        for t, q in self.task_qualities:
            if t == task_type:
                return q
        return tier_default


# Shared task_qualities for model families — avoids duplication across aliases.
_OPUS_46_QUALITIES: tuple[tuple[str, float], ...] = (
    ("classification", 0.96), ("code_generation", 0.93), ("code_review", 0.91),
    ("conversation", 0.95), ("extraction", 0.94), ("reasoning", 0.93),
    ("summarization", 0.94), ("tool_selection", 0.92),
)
_OPUS_45_QUALITIES: tuple[tuple[str, float], ...] = (
    ("classification", 0.94), ("code_generation", 0.90), ("code_review", 0.88),
    ("conversation", 0.93), ("extraction", 0.92), ("reasoning", 0.91),
    ("summarization", 0.92), ("tool_selection", 0.90),
)
_SONNET_4_QUALITIES: tuple[tuple[str, float], ...] = (
    ("classification", 0.87), ("code_generation", 0.75), ("code_review", 0.70),
    ("conversation", 0.81), ("extraction", 0.85), ("reasoning", 0.72),
    ("summarization", 0.80), ("tool_selection", 0.77),
)
_HAIKU_QUALITIES: tuple[tuple[str, float], ...] = (
    ("classification", 0.72), ("code_generation", 0.38), ("code_review", 0.35),
    ("conversation", 0.68), ("extraction", 0.73), ("reasoning", 0.40),
    ("summarization", 0.65), ("tool_selection", 0.58),
)
_BUDGET_QUALITIES: tuple[tuple[str, float], ...] = (
    ("classification", 0.72), ("code_generation", 0.35), ("code_review", 0.32),
    ("conversation", 0.68), ("extraction", 0.73), ("reasoning", 0.40),
    ("summarization", 0.65), ("tool_selection", 0.58),
)

MODEL_CATALOG: dict[str, ModelInfo] = {
    # ── Tier 1: Opus-class / frontier ───────────────────────────────
    "claude-opus-4-20250514": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_46_QUALITIES,
    ),
    "claude-opus-4-6-20250527": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_46_QUALITIES,
    ),
    "claude-opus-4-6": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_46_QUALITIES,
    ),
    "claude-opus-4-5-20251101": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_45_QUALITIES,
    ),
    "claude-opus-4-5": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_45_QUALITIES,
    ),
    "us.anthropic.claude-opus-4-5-20251101-v1:0": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=_OPUS_45_QUALITIES,
    ),
    # ── Tier 1: GPT-5.2 (promoted from tier 2) ─────────────────────
    "gpt-5.2-chat-latest": ModelInfo(
        tier=1,
        cost_per_1k_input=0.00175,
        cost_per_1k_output=0.014,
        downgrade_to="claude-sonnet-4-6",
        task_qualities=(
            ("classification", 0.88), ("code_generation", 0.76), ("code_review", 0.72),
            ("conversation", 0.84), ("extraction", 0.85), ("reasoning", 0.73),
            ("summarization", 0.78), ("tool_selection", 0.75),
        ),
    ),
    # ── Tier 2: Sonnet / strong mid-tier ──────────────────────────
    "claude-sonnet-4-6": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=(
            ("classification", 0.88), ("code_generation", 0.76), ("code_review", 0.72),
            ("conversation", 0.82), ("extraction", 0.87), ("reasoning", 0.74),
            ("summarization", 0.82), ("tool_selection", 0.75),
        ),
    ),
    "claude-sonnet-4-5-20250929": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=_SONNET_4_QUALITIES,
    ),
    "claude-sonnet-4-20250514": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=_SONNET_4_QUALITIES,
    ),
    "gpt-5.2-codex": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00175,
        cost_per_1k_output=0.014,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=(
            ("classification", 0.82), ("code_generation", 0.80), ("code_review", 0.76),
            ("conversation", 0.72), ("extraction", 0.78), ("reasoning", 0.77),
            ("summarization", 0.74), ("tool_selection", 0.80),
        ),
    ),
    "gpt-4o": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=(
            ("classification", 0.87), ("code_generation", 0.73), ("code_review", 0.72),
            ("conversation", 0.81), ("extraction", 0.85), ("reasoning", 0.72),
            ("summarization", 0.82), ("tool_selection", 0.75),
        ),
    ),
    "gpt-4-turbo": ModelInfo(
        tier=2,
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        downgrade_to="claude-haiku-4-5-20251001",
        task_qualities=(
            ("classification", 0.85), ("code_generation", 0.72), ("code_review", 0.70),
            ("conversation", 0.80), ("extraction", 0.83), ("reasoning", 0.70),
            ("summarization", 0.78), ("tool_selection", 0.73),
        ),
    ),
    "qwen.qwen3-vl-235b-a22b": ModelInfo(
        tier=2,
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.01,
        downgrade_to="qwen.qwen3-next-80b-a3b",
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.87), ("code_generation", 0.76), ("code_review", 0.74),
            ("conversation", 0.81), ("extraction", 0.85), ("reasoning", 0.72),
            ("summarization", 0.78), ("tool_selection", 0.77),
        ),
    ),
    "qwen.qwen3-next-80b-a3b": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0012,
        cost_per_1k_output=0.006,
        downgrade_to="qwen.qwen3-coder-30b-a3b-v1:0",
        supports_tool_use=False,
    ),
    "moonshot.kimi-k2-thinking": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0006,
        cost_per_1k_output=0.0025,
        downgrade_to="moonshotai.kimi-k2.5",
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.85), ("code_generation", 0.76), ("code_review", 0.72),
            ("conversation", 0.81), ("extraction", 0.84), ("reasoning", 0.74),
            ("summarization", 0.80), ("tool_selection", 0.72),
        ),
    ),
    "moonshotai.kimi-k2.5": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0006,
        cost_per_1k_output=0.003,
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.84), ("code_generation", 0.74), ("code_review", 0.70),
            ("conversation", 0.80), ("extraction", 0.83), ("reasoning", 0.72),
            ("summarization", 0.78), ("tool_selection", 0.72),
        ),
    ),
    "openai.gpt-oss-120b-1:0": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00069,
        downgrade_to="openai.gpt-oss-20b-1:0",
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.87), ("code_generation", 0.76), ("code_review", 0.74),
            ("conversation", 0.81), ("extraction", 0.84), ("reasoning", 0.72),
            ("summarization", 0.83), ("tool_selection", 0.75),
        ),
    ),
    "minimax.minimax-m2.1": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00027,
        cost_per_1k_output=0.00095,
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.85), ("code_generation", 0.76), ("code_review", 0.72),
            ("conversation", 0.81), ("extraction", 0.85), ("reasoning", 0.70),
            ("summarization", 0.80), ("tool_selection", 0.77),
        ),
    ),
    # ── Tier 3: Haiku / mini / small open-source ────────────────────
    "claude-haiku-4-5-20251001": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        supports_thinking=False,
        task_qualities=_HAIKU_QUALITIES,
    ),
    # gpt-4o-mini not available in LiteLLM — kept for cost calculations
    # if events reference it, but routing won't select it.
    "gpt-4o-mini": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        task_qualities=_HAIKU_QUALITIES,
    ),
    "us.amazon.nova-2-lite-v1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0003,
        cost_per_1k_output=0.0025,
        supports_tool_use=False,
        task_qualities=_BUDGET_QUALITIES,
    ),
    "qwen.qwen3-coder-30b-a3b-v1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00022,
        cost_per_1k_output=0.001,
        supports_tool_use=False,
    ),
    "openai.gpt-oss-20b-1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00003,
        cost_per_1k_output=0.00014,
        supports_tool_use=False,
        task_qualities=(
            ("classification", 0.68), ("code_generation", 0.32), ("code_review", 0.30),
            ("conversation", 0.60), ("extraction", 0.65), ("reasoning", 0.35),
            ("summarization", 0.58), ("tool_selection", 0.48),
        ),
    ),
    "google.gemma-3-27b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00015,
        downgrade_to="google.gemma-3-12b-it",
        supports_tool_use=False,
        task_qualities=_BUDGET_QUALITIES,
    ),
    "google.gemma-3-12b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00013,
        downgrade_to="google.gemma-3-4b-it",
        supports_tool_use=False,
    ),
    "google.gemma-3-4b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00008,
        supports_tool_use=False,
    ),
    "mistral.ministral-3-14b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0002,
        downgrade_to="mistral.ministral-3-8b-instruct",
        supports_tool_use=False,
    ),
    "mistral.ministral-3-8b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00015,
        downgrade_to="mistral.ministral-3-3b-instruct",
        supports_tool_use=False,
        task_qualities=_BUDGET_QUALITIES,
    ),
    "mistral.ministral-3-3b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0001,
        supports_tool_use=False,
    ),
}


# Import-time guard: catch TaskType enum drift immediately
def _check_task_type_sync() -> None:
    from blockthrough.types import TaskType
    enum_values = {t.value for t in TaskType if t.value != "unknown"}
    if enum_values != _VALID_TASK_TYPES:
        missing = enum_values - _VALID_TASK_TYPES
        extra = _VALID_TASK_TYPES - enum_values
        raise RuntimeError(
            f"_VALID_TASK_TYPES out of sync with TaskType enum. "
            f"Missing: {missing}, Extra: {extra}"
        )

_check_task_type_sync()


def get_anthropic_models() -> set[str]:
    """Return model names from MODEL_CATALOG that are Anthropic-native (claude)."""
    return {name for name in MODEL_CATALOG if "claude" in name}


def get_downgrade(model: str) -> str | None:
    """Return the next-cheaper model, or None if already at the bottom / unknown."""
    info = MODEL_CATALOG.get(model)
    return info.downgrade_to if info else None


def get_tier(model: str) -> int | None:
    """Return the tier number (1=expensive … 3=cheap), or None for unknown models."""
    info = MODEL_CATALOG.get(model)
    return info.tier if info else None
