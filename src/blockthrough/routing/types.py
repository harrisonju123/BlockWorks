"""Routing subsystem types.

These models define the contract between the policy DSL, the router engine,
the dry-run simulator, and the A/B testing framework. The routing layer
provides decisions but does NOT modify LiteLLM behavior directly -- that
integration is a follow-up.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field

from blockthrough.types import TaskType


class SelectionCriteria(str, enum.Enum):
    """How the router picks among qualified models."""

    CHEAPEST_ABOVE_QUALITY = "cheapest_above_quality"
    FASTEST_ABOVE_QUALITY = "fastest_above_quality"
    HIGHEST_QUALITY_UNDER_COST = "highest_quality_under_cost"
    BEST_VALUE = "best_value"


class RoutingRule(BaseModel):
    """One rule within a routing policy.

    task_type "*" is the wildcard catch-all, matched when no specific
    rule exists for the incoming task type.
    """

    task_type: str  # TaskType value or "*" for catch-all
    criteria: SelectionCriteria
    min_quality: float = Field(ge=0.0, le=1.0, default=0.8)
    max_cost_per_1k: float | None = None
    max_latency_ms: float | None = None
    fallback: str

    @property
    def is_catch_all(self) -> bool:
        return self.task_type == "*"


class RoutingPolicy(BaseModel):
    """Collection of rules loaded from YAML.

    Rules are evaluated in order; the first matching rule wins.
    A catch-all rule (task_type="*") should be last.
    """

    rules: list[RoutingRule] = Field(default_factory=list)
    version: int = 1


class RoutingDecision(BaseModel):
    """Result of a routing resolution."""

    selected_model: str
    reason: str
    was_overridden: bool  # True when the selected model differs from requested
    policy_rule_id: int | None = None  # index of the matched rule, None if passthrough
    group: str | None = None  # A/B test group tag ("control" or "experiment")


# Absolute quality floor enforced regardless of policy settings.
# Prevents routing to models with dangerously low quality scores.
QUALITY_FLOOR = 0.30
