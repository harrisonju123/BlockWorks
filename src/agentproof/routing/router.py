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
from agentproof.models import MODEL_CATALOG, ModelInfo
from agentproof.routing.types import (
    QUALITY_FLOOR,
    RoutingDecision,
    RoutingPolicy,
    RoutingRule,
    SelectionCriteria,
)
from agentproof.types import TaskType

# Synthetic fitness defaults by model tier — used when no benchmark data exists
_TIER_DEFAULTS: dict[int, dict[str, float]] = {
    1: {"quality": 0.95, "latency": 2000.0},
    2: {"quality": 0.85, "latency": 1000.0},
    3: {"quality": 0.75, "latency": 500.0},
}


def _get_tier_models() -> dict[int, str]:
    """Return user's configured tier->model mapping."""
    from agentproof.config import get_config
    cfg = get_config()
    return {1: cfg.routing_model_high, 2: cfg.routing_model_mid, 3: cfg.routing_model_low}


def generate_synthetic_fitness() -> list[FitnessEntry]:
    """Build synthetic FitnessEntry objects for the 3 configured tier models.

    Quality comes from the assigned tier (high=0.95, mid=0.85, low=0.75),
    cost from MODEL_CATALOG. sample_size=0 marks them as synthetic so real
    benchmark data overwrites them via merge_fitness_entries.
    """
    tier_models = _get_tier_models()
    entries: list[FitnessEntry] = []
    task_types = [t for t in TaskType if t != TaskType.UNKNOWN]

    for tier, model_name in tier_models.items():
        info = MODEL_CATALOG.get(model_name)
        if info is None:
            continue
        defaults = _TIER_DEFAULTS.get(tier)
        if defaults is None:
            continue
        for task_type in task_types:
            entries.append(
                FitnessEntry(
                    task_type=task_type.value,
                    model=model_name,
                    avg_quality=defaults["quality"],
                    avg_cost=info.avg_cost,
                    avg_latency=defaults["latency"],
                    sample_size=0,
                )
            )
    return entries


def merge_fitness_entries(
    synthetic: list[FitnessEntry], real: list[FitnessEntry]
) -> list[FitnessEntry]:
    """Merge synthetic base with real benchmark data.

    Synthetic entries provide the baseline for the 3 tier models. Real entries
    with sample_size > 0 overwrite tier-model synthetics and introduce new
    models discovered via benchmarking.

    Quality and latency come from real benchmarks. Cost always comes from
    MODEL_CATALOG's per-1k-token pricing (DB benchmark_cost is per-request
    and can't be compared across models for routing).
    """
    by_key: dict[tuple[str, str], FitnessEntry] = {
        (e.model, e.task_type): e for e in synthetic
    }
    for entry in real:
        if entry.sample_size > 0:
            info = MODEL_CATALOG.get(entry.model)
            normalized_cost = info.avg_cost if info is not None else entry.avg_cost
            by_key[(entry.model, entry.task_type)] = FitnessEntry(
                task_type=entry.task_type,
                model=entry.model,
                avg_quality=entry.avg_quality,
                avg_cost=normalized_cost,
                avg_latency=entry.avg_latency,
                sample_size=entry.sample_size,
            )
    return list(by_key.values())


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

    def get_all_entries(self) -> list[FitnessEntry]:
        """Return a snapshot of all cached entries."""
        return list(self._entries)

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

    elif rule.criteria == SelectionCriteria.BEST_VALUE:
        # Quality per unit cost — higher is better
        viable = sorted(
            candidates,
            key=lambda e: e.avg_quality / max(e.avg_cost, 1e-7),
            reverse=True,
        )

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
    *,
    has_tool_use: bool = False,
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

    # Exclude models that don't support tool use when the request has tools
    if has_tool_use:
        _DEFAULT_INFO = ModelInfo(tier=3, cost_per_1k_input=0, cost_per_1k_output=0)
        qualified = [
            c for c in qualified
            if MODEL_CATALOG.get(c.model, _DEFAULT_INFO).supports_tool_use
        ]

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
