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

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.models import MODEL_CATALOG, ModelInfo
from blockthrough.routing.policy import _HARD_TASKS
from blockthrough.routing.types import (
    QUALITY_FLOOR,
    RoutingDecision,
    RoutingPolicy,
    RoutingRule,
    SelectionCriteria,
)
from blockthrough.types import TaskType

# Pre-compute string values for fast membership check in resolve()
_HARD_TASK_VALUES: frozenset[str] = frozenset(t.value for t in _HARD_TASKS)

_UNKNOWN_MODEL_INFO = ModelInfo(tier=3, cost_per_1k_input=0, cost_per_1k_output=0, supports_tool_use=False)

# Synthetic fitness defaults by model tier — used when no benchmark data exists
_TIER_DEFAULTS: dict[int, dict[str, float]] = {
    1: {"quality": 0.93, "latency": 2000.0},
    2: {"quality": 0.79, "latency": 1000.0},
    3: {"quality": 0.56, "latency": 500.0},
}


def generate_synthetic_fitness() -> list[FitnessEntry]:
    """Build synthetic FitnessEntry objects for all models in the catalog.

    Quality comes from per-model task_qualities when available, falling back
    to tier defaults (high=0.93, mid=0.79, low=0.56). This lets the router
    differentiate models within the same tier — e.g. Sonnet-4-6 scores 0.74
    on reasoning while budget models score 0.35-0.40.

    sample_size=0 marks entries as synthetic so real benchmark data
    overwrites them via merge_fitness_entries.
    """
    entries: list[FitnessEntry] = []
    task_types = [t for t in TaskType if t != TaskType.UNKNOWN]

    for model_name, info in MODEL_CATALOG.items():
        defaults = _TIER_DEFAULTS.get(info.tier)
        if defaults is None:
            continue
        tier_quality = defaults["quality"]
        tier_latency = defaults["latency"]

        for task_type in task_types:
            quality = info.quality_for_task(task_type.value, tier_quality)
            entries.append(
                FitnessEntry(
                    task_type=task_type.value,
                    model=model_name,
                    avg_quality=quality,
                    avg_cost=info.avg_cost,
                    avg_latency=tier_latency,
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
        self._force_stale: bool = False
        # Pre-built index: task_type -> list of entries, sorted by quality desc
        self._by_task_type: dict[str, list[FitnessEntry]] = {}

    def update(self, entries: list[FitnessEntry]) -> None:
        """Replace the cached fitness matrix and rebuild the index.

        Builds the new index into local vars before swapping references so
        a concurrent reader never sees a half-built (empty) index.
        """
        new_entries = list(entries)
        new_index: dict[str, list[FitnessEntry]] = {}
        for entry in new_entries:
            new_index.setdefault(entry.task_type, []).append(entry)
        for task_type in new_index:
            new_index[task_type].sort(key=lambda e: e.avg_quality, reverse=True)
        # Single-assignment swap — safe under asyncio's single-threaded event loop
        self._entries = new_entries
        self._by_task_type = new_index
        self._last_updated = time.monotonic()
        self._force_stale = False

    def mark_stale(self) -> None:
        """Signal that new benchmark data is available, triggering an early refresh."""
        self._force_stale = True

    @property
    def is_stale(self) -> bool:
        if self._force_stale or self._last_updated == 0.0:
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
        # Already sorted by quality descending from FitnessCache
        viable = candidates

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
    allowed_models: set[str] | None = None,
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

    # Tier-1 preservation: when the requested model is frontier-class and the
    # task is hard, switch to quality-first selection so BEST_VALUE's cost bias
    # doesn't always downgrade Opus to Sonnet.
    requested_info = MODEL_CATALOG.get(requested_model)
    if (
        requested_info is not None
        and requested_info.tier == 1
        and not rule.is_catch_all
        and task_type in _HARD_TASK_VALUES
    ):
        rule = RoutingRule(
            task_type=rule.task_type,
            criteria=SelectionCriteria.HIGHEST_QUALITY_UNDER_COST,
            min_quality=max(rule.min_quality, 0.85),
            max_cost_per_1k=None,
            max_latency_ms=rule.max_latency_ms,
            fallback=rule.fallback,
        )

    # Empty fitness matrix -- fall through to fallback
    if fitness_cache.is_empty:
        fallback = rule.fallback
        if allowed_models is not None and fallback not in allowed_models:
            fallback = requested_model
        return RoutingDecision(
            selected_model=fallback,
            reason="fitness matrix empty, using fallback",
            was_overridden=fallback != requested_model,
            policy_rule_id=rule_index,
        )

    candidates = fitness_cache.get_entries_for_task(task_type)

    # Filter to allowed models (e.g. Anthropic-only for /v1/messages)
    if allowed_models is not None:
        candidates = [c for c in candidates if c.model in allowed_models]

    # Filter by quality: enforce both the rule's min_quality and the global floor
    effective_min_quality = max(rule.min_quality, QUALITY_FLOOR)
    qualified = [c for c in candidates if c.avg_quality >= effective_min_quality]

    # Exclude models that don't support tool use when the request has tools
    if has_tool_use:
        qualified = [
            c for c in qualified
            if MODEL_CATALOG.get(c.model, _UNKNOWN_MODEL_INFO).supports_tool_use
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
    fallback = rule.fallback
    if allowed_models is not None and fallback not in allowed_models:
        fallback = requested_model
    return RoutingDecision(
        selected_model=fallback,
        reason=(
            f"rule[{rule_index}] no model met criteria "
            f"(min_quality={effective_min_quality}), using fallback"
        ),
        was_overridden=fallback != requested_model,
        policy_rule_id=rule_index,
    )
