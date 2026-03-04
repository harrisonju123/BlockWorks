"""Consolidated model catalog — single source of truth for pricing, tiers, and downgrades.

Merges the former MODEL_COST_TIERS (waste.py) and MODEL_DOWNGRADE_MAP (budgets.py)
into one registry. Every module that needs model metadata should import from here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    """Pricing, tier placement, and downgrade path for one model."""

    tier: int
    cost_per_1k_input: float
    cost_per_1k_output: float
    downgrade_to: str | None = None

    @property
    def avg_cost(self) -> float:
        return (self.cost_per_1k_input + self.cost_per_1k_output) / 2


MODEL_CATALOG: dict[str, ModelInfo] = {
    # ── Tier 1: Opus-class / frontier ───────────────────────────────
    "claude-opus-4-20250514": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    "claude-opus-4-6-20250527": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    "claude-opus-4-6": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    "claude-opus-4-5-20251101": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    "claude-opus-4-5": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    "us.anthropic.claude-opus-4-5-20251101-v1:0": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-6",
    ),
    # ── Tier 2: Sonnet / GPT-5.2 / strong mid-tier ─────────────────
    "claude-sonnet-4-6": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "claude-sonnet-4-5-20250929": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "claude-sonnet-4-20250514": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "gpt-5.2-chat-latest": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00175,
        cost_per_1k_output=0.014,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "gpt-5.2-codex": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00175,
        cost_per_1k_output=0.014,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "gpt-4o": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "gpt-4-turbo": ModelInfo(
        tier=2,
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    "qwen.qwen3-vl-235b-a22b": ModelInfo(
        tier=2,
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.01,
        downgrade_to="qwen.qwen3-next-80b-a3b",
    ),
    "qwen.qwen3-next-80b-a3b": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0012,
        cost_per_1k_output=0.006,
        downgrade_to="qwen.qwen3-coder-30b-a3b-v1:0",
    ),
    "moonshot.kimi-k2-thinking": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0006,
        cost_per_1k_output=0.0025,
        downgrade_to="moonshotai.kimi-k2.5",
    ),
    "moonshotai.kimi-k2.5": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0006,
        cost_per_1k_output=0.003,
    ),
    "openai.gpt-oss-120b-1:0": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00069,
        downgrade_to="openai.gpt-oss-20b-1:0",
    ),
    "minimax.minimax-m2.1": ModelInfo(
        tier=2,
        cost_per_1k_input=0.00027,
        cost_per_1k_output=0.00095,
    ),
    # ── Tier 3: Haiku / mini / small open-source ────────────────────
    "claude-haiku-4-5-20251001": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
    ),
    # gpt-4o-mini not available in LiteLLM — kept for cost calculations
    # if events reference it, but routing won't select it.
    "gpt-4o-mini": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
    ),
    "us.amazon.nova-2-lite-v1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0003,
        cost_per_1k_output=0.0025,
    ),
    "qwen.qwen3-coder-30b-a3b-v1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00022,
        cost_per_1k_output=0.001,
    ),
    "openai.gpt-oss-20b-1:0": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00003,
        cost_per_1k_output=0.00014,
    ),
    "google.gemma-3-27b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00015,
        downgrade_to="google.gemma-3-12b-it",
    ),
    "google.gemma-3-12b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00013,
        downgrade_to="google.gemma-3-4b-it",
    ),
    "google.gemma-3-4b-it": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00004,
        cost_per_1k_output=0.00008,
    ),
    "mistral.ministral-3-14b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0002,
        downgrade_to="mistral.ministral-3-8b-instruct",
    ),
    "mistral.ministral-3-8b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00015,
        downgrade_to="mistral.ministral-3-3b-instruct",
    ),
    "mistral.ministral-3-3b-instruct": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0001,
    ),
}


def get_downgrade(model: str) -> str | None:
    """Return the next-cheaper model, or None if already at the bottom / unknown."""
    info = MODEL_CATALOG.get(model)
    return info.downgrade_to if info else None


def get_tier(model: str) -> int | None:
    """Return the tier number (1=expensive … 3=cheap), or None for unknown models."""
    info = MODEL_CATALOG.get(model)
    return info.tier if info else None
