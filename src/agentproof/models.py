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
    # ── Tier 1: Opus-class ──────────────────────────────────────────
    "claude-opus-4-20250514": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-20250514",
    ),
    "claude-opus-4-6-20250527": ModelInfo(
        tier=1,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        downgrade_to="claude-sonnet-4-20250514",
    ),
    # ── Tier 2: Sonnet / GPT-4o class ──────────────────────────────
    "gpt-4o": ModelInfo(
        tier=2,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
        downgrade_to="gpt-4o-mini",
    ),
    "gpt-4-turbo": ModelInfo(
        tier=2,
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        downgrade_to="gpt-4o-mini",
    ),
    "claude-sonnet-4-20250514": ModelInfo(
        tier=2,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        downgrade_to="claude-haiku-4-5-20251001",
    ),
    # ── Tier 3: Haiku / mini class (no further downgrade) ──────────
    "claude-haiku-4-5-20251001": ModelInfo(
        tier=3,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
    ),
    "gpt-4o-mini": ModelInfo(
        tier=3,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
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
