"""A/B testing framework for routing policies.

Deterministically assigns each trace to a control or experiment group
based on a hash of the trace_id. This ensures consistent assignment
across requests within the same trace, and reproducible splits for
analysis.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from agentproof.routing.policy import default_policy
from agentproof.routing.types import RoutingPolicy


class ABTestConfig(BaseModel):
    """Configuration for a routing A/B test."""

    policy_a: RoutingPolicy = Field(
        default_factory=default_policy,
        description="Control group policy (typically the default/current policy)",
    )
    policy_b: RoutingPolicy = Field(
        default_factory=default_policy,
        description="Experiment group policy (the new policy being tested)",
    )
    split_ratio: float = Field(
        ge=0.0, le=1.0, default=0.5,
        description="Fraction of traffic routed to policy_b (experiment group)",
    )
    enabled: bool = True


def assign_group(trace_id: str, split_ratio: float = 0.5) -> str:
    """Deterministically assign a trace to 'control' or 'experiment'.

    Uses a stable hash of the trace_id so the same trace always lands
    in the same group, even across restarts. The hash output is uniform
    so the split ratio is respected at scale.
    """
    digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
    # Use the first 8 hex chars (32 bits) for a uniform float in [0, 1)
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "experiment" if bucket < split_ratio else "control"


def get_policy(trace_id: str, config: ABTestConfig) -> tuple[RoutingPolicy, str]:
    """Return the policy and group tag for this trace.

    When the A/B test is disabled, always returns policy_a (control).
    """
    if not config.enabled:
        return config.policy_a, "control"

    group = assign_group(trace_id, config.split_ratio)
    if group == "experiment":
        return config.policy_b, "experiment"
    return config.policy_a, "control"
