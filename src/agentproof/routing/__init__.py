"""Smart routing engine -- policy-driven model selection using the fitness matrix.

Public API:
    - load_policy / validate_policy: parse and validate YAML routing policies
    - resolve: make a routing decision for a given task type
    - dry_run: simulate routing decisions against historical data
    - ABTestConfig / assign_group / get_policy: A/B testing framework
"""

from agentproof.routing.types import (
    QUALITY_FLOOR,
    RoutingDecision,
    RoutingPolicy,
    RoutingRule,
    SelectionCriteria,
)

__all__ = [
    "QUALITY_FLOOR",
    "RoutingDecision",
    "RoutingPolicy",
    "RoutingRule",
    "SelectionCriteria",
]
