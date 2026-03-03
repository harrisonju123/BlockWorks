"""Core routing engine -- resolves task type + policy + fitness matrix into a model decision.

Design constraints:
    - <2ms decision latency: fitness matrix is cached in-process, not fetched per request
    - Quality floor: never route below QUALITY_FLOOR regardless of policy
    - Fallback: if no model meets criteria, use the rule's fallback model
    - Passthrough: if no rule matches, return the originally requested model unchanged
"""

from __future__ import annotations

import time
from typing import Any

from agentproof.benchmarking.types import FitnessEntry
from agentproof.routing.types import (
    QUALITY_FLOOR,
    RoutingDecision,
    RoutingPolicy,
    RoutingRule,
    SelectionCriteria,
)


class FitnessCache:
    """In-process cache for the fitness matrix to avoid DB calls per decision.

    The caller is responsible for refreshing via `update()`. The router
    reads from the cache via `get_entries()`.
    """

    def __init__(self, ttl_s: int = 300) -> None:
        self._ttl_s = ttl_s
        self._entries: list[FitnessEntry] = []
        self._last_updated: float = 0.0
        # Pre-built index: task_type -> list of entries, sorted by quality desc
        self._by_task_type: dict[str, list[FitnessEntry]] = {}

    def update(self, entries: list[FitnessEntry]) -> None:
        """Replace the cached fitness matrix and rebuild the index."""
        self._entries = list(entries)
        self._last_updated = time.monotonic()
        self._by_task_type = {}
        for entry in self._entries:
            self._by_task_type.setdefault(entry.task_type, []).append(entry)
        # Pre-sort each task type's entries by quality descending for fast filtering
        for task_type in self._by_task_type:
            self._by_task_type[task_type].sort(key=lambda e: e.avg_quality, reverse=True)

    @property
    def is_stale(self) -> bool:
        if self._last_updated == 0.0:
            return True
        return (time.monotonic() - self._last_updated) > self._ttl_s

    @property
    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def get_entries_for_task(self, task_type: str) -> list[FitnessEntry]:
        """Return cached entries for a task type, already sorted by quality desc."""
        return self._by_task_type.get(task_type, [])


def _find_matching_rule(
    task_type: str, policy: RoutingPolicy
) -> tuple[RoutingRule | None, int | None]:
    """Walk the policy rules in order, return first match and its index."""
    for i, rule in enumerate(policy.rules):
        if rule.task_type == task_type or rule.is_catch_all:
            return rule, i
    return None, None


def _apply_criteria(
    rule: RoutingRule,
    candidates: list[FitnessEntry],
) -> FitnessEntry | None:
    """Filter and rank candidates according to the rule's selection criteria.

    Candidates have already been filtered to meet quality thresholds by the caller.
    """
    if not candidates:
        return None

    if rule.criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY:
        # Sort by cost ascending, pick cheapest
        viable = sorted(candidates, key=lambda e: e.avg_cost)

    elif rule.criteria == SelectionCriteria.FASTEST_ABOVE_QUALITY:
        # Sort by latency ascending, pick fastest
        viable = sorted(candidates, key=lambda e: e.avg_latency)

    elif rule.criteria == SelectionCriteria.HIGHEST_QUALITY_UNDER_COST:
        # Sort by quality descending (already sorted this way from cache)
        viable = sorted(candidates, key=lambda e: e.avg_quality, reverse=True)

    else:
        return None

    # Apply optional hard constraints
    for entry in viable:
        if rule.max_cost_per_1k is not None and entry.avg_cost > rule.max_cost_per_1k:
            continue
        if rule.max_latency_ms is not None and entry.avg_latency > rule.max_latency_ms:
            continue
        return entry

    return None


def resolve(
    task_type: str,
    requested_model: str,
    fitness_cache: FitnessCache,
    policy: RoutingPolicy,
) -> RoutingDecision:
    """Make a routing decision for the given task type and policy.

    If no rule matches or the fitness matrix is empty, the originally
    requested model is returned unchanged (passthrough).
    """
    # Passthrough: empty policy means no routing
    if not policy.rules:
        return RoutingDecision(
            selected_model=requested_model,
            reason="passthrough: empty policy",
            was_overridden=False,
            policy_rule_id=None,
        )

    rule, rule_index = _find_matching_rule(task_type, policy)

    if rule is None:
        return RoutingDecision(
            selected_model=requested_model,
            reason=f"no matching rule for task_type={task_type}",
            was_overridden=False,
            policy_rule_id=None,
        )

    # Empty fitness matrix -- fall through to fallback
    if fitness_cache.is_empty:
        return RoutingDecision(
            selected_model=rule.fallback,
            reason="fitness matrix empty, using fallback",
            was_overridden=rule.fallback != requested_model,
            policy_rule_id=rule_index,
        )

    candidates = fitness_cache.get_entries_for_task(task_type)

    # Filter by quality: enforce both the rule's min_quality and the global floor
    effective_min_quality = max(rule.min_quality, QUALITY_FLOOR)
    qualified = [c for c in candidates if c.avg_quality >= effective_min_quality]

    best = _apply_criteria(rule, qualified)

    if best is not None:
        return RoutingDecision(
            selected_model=best.model,
            reason=(
                f"rule[{rule_index}] criteria={rule.criteria.value} "
                f"quality={best.avg_quality:.3f} cost={best.avg_cost:.6f}"
            ),
            was_overridden=best.model != requested_model,
            policy_rule_id=rule_index,
        )

    # No candidate met criteria -- use fallback
    return RoutingDecision(
        selected_model=rule.fallback,
        reason=(
            f"rule[{rule_index}] no model met criteria "
            f"(min_quality={effective_min_quality}), using fallback"
        ),
        was_overridden=rule.fallback != requested_model,
        policy_rule_id=rule_index,
    )
