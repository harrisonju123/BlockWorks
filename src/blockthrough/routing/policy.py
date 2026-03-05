"""YAML-based routing policy loader and validator.

The policy DSL lets operators define per-task-type model selection rules
without touching code. Validation catches misconfiguration at load time
rather than at request time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from blockthrough.models import MODEL_CATALOG
from blockthrough.routing.types import QUALITY_FLOOR, RoutingPolicy, RoutingRule, SelectionCriteria
from blockthrough.types import TaskType

# All task_type values the router recognizes, plus the wildcard
_VALID_TASK_TYPES: set[str] = {t.value for t in TaskType} | {"*"}

# Models we know about from the model catalog
KNOWN_MODELS: set[str] = set(MODEL_CATALOG.keys())


class PolicyValidationError(Exception):
    """Raised when a routing policy fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Policy validation failed: {'; '.join(errors)}")


def load_policy(source: str | Path | dict[str, Any]) -> RoutingPolicy:
    """Parse a routing policy from a YAML file path, YAML string, or dict.

    Validates after parsing. Raises PolicyValidationError on invalid input.
    """
    if isinstance(source, dict):
        raw = source
    elif isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and not source.strip().startswith("{")):
        # Treat as file path if it's a Path or a single-line string that isn't JSON-like
        path = Path(source) if isinstance(source, str) else source
        if path.exists():
            raw = yaml.safe_load(path.read_text())
        else:
            # Try parsing as YAML string (for single-line YAML)
            raw = yaml.safe_load(source)
    else:
        raw = yaml.safe_load(source)

    if raw is None:
        raw = {}

    policy = RoutingPolicy(**raw)
    validate_policy(policy)
    return policy


def validate_policy(policy: RoutingPolicy) -> None:
    """Check that all models exist in known models and thresholds are sane.

    Raises PolicyValidationError with a list of all violations found.
    """
    errors: list[str] = []

    seen_task_types: set[str] = set()
    catch_all_index: int | None = None

    for i, rule in enumerate(policy.rules):
        # Validate task_type is recognized
        if rule.task_type not in _VALID_TASK_TYPES:
            errors.append(
                f"Rule {i}: unknown task_type '{rule.task_type}'. "
                f"Valid values: {sorted(_VALID_TASK_TYPES)}"
            )

        # Warn on duplicate task_type (first match wins, so duplicates are dead code)
        if rule.task_type in seen_task_types:
            errors.append(
                f"Rule {i}: duplicate task_type '{rule.task_type}'. "
                "Only the first matching rule is used."
            )
        seen_task_types.add(rule.task_type)

        # Catch-all must be last
        if rule.is_catch_all:
            catch_all_index = i
        elif catch_all_index is not None:
            errors.append(
                f"Rule {i}: rules after catch-all ('*') at index {catch_all_index} "
                "will never be reached."
            )

        # Validate fallback model is known
        if rule.fallback not in KNOWN_MODELS:
            errors.append(
                f"Rule {i}: fallback model '{rule.fallback}' not in known models. "
                f"Known: {sorted(KNOWN_MODELS)}"
            )

        # Validate cost constraint is positive
        if rule.max_cost_per_1k is not None and rule.max_cost_per_1k <= 0:
            errors.append(f"Rule {i}: max_cost_per_1k must be positive, got {rule.max_cost_per_1k}")

        # Validate latency constraint is positive
        if rule.max_latency_ms is not None and rule.max_latency_ms <= 0:
            errors.append(f"Rule {i}: max_latency_ms must be positive, got {rule.max_latency_ms}")

    if errors:
        raise PolicyValidationError(errors)


# Complex task types that get higher quality thresholds and mid-tier fallbacks
_COMPLEX_TASKS: set[TaskType] = {
    TaskType.CODE_GENERATION, TaskType.CODE_REVIEW,
    TaskType.SUMMARIZATION, TaskType.REASONING,
}


def _compute_task_threshold(entries: list[Any]) -> float:
    """Derive min_quality from actual benchmark data for a task type.

    Uses upper-median quality of benchmarked models so at least ceil(N/2)
    models qualify. Clamps between QUALITY_FLOOR (0.7) and 0.95.
    """
    real = [e for e in entries if e.sample_size > 0]
    if not real:
        return 0.8
    qualities = sorted(e.avg_quality for e in real)
    # Upper-median: ensures at least half the models meet the threshold
    median_q = qualities[len(qualities) // 2]
    return max(QUALITY_FLOOR, min(0.95, round(median_q, 3)))


# Cache keyed by config tuple + fitness cache timestamp for self-invalidation.
_cached_bootstrap: dict[tuple[str, str, str, float], RoutingPolicy] = {}


def clear_bootstrap_cache() -> None:
    """Invalidate the cached bootstrap policy. Call when tier config changes."""
    _cached_bootstrap.clear()


def bootstrap_policy(fitness_cache: Any = None) -> RoutingPolicy:
    """Config-driven default policy using the 3 user-selected tier models.

    When fitness_cache has real benchmark data, uses BEST_VALUE criteria with
    data-derived quality thresholds. Otherwise falls back to static
    CHEAPEST_ABOVE_QUALITY with hardcoded thresholds.

    Version=0 signals system-generated.
    Cached per (config tuple, fitness timestamp) — stale entries auto-evict.
    """
    from blockthrough.config import get_config
    cfg = get_config()
    has_fitness = fitness_cache is not None and not fitness_cache.is_empty
    # Use _last_updated timestamp so different fitness data produces different keys
    fitness_ts = getattr(fitness_cache, "_last_updated", 0.0) if has_fitness else 0.0

    cache_key = (cfg.routing_model_high, cfg.routing_model_mid, cfg.routing_model_low, fitness_ts)
    if cache_key in _cached_bootstrap:
        return _cached_bootstrap[cache_key]

    rules: list[RoutingRule] = []
    task_types = [t for t in TaskType if t != TaskType.UNKNOWN]

    for task in task_types:
        if has_fitness:
            entries = fitness_cache.get_entries_for_task(task.value)
            min_q = _compute_task_threshold(entries)
            criteria = SelectionCriteria.BEST_VALUE
        else:
            min_q = 0.85 if task in _COMPLEX_TASKS else 0.8
            criteria = SelectionCriteria.CHEAPEST_ABOVE_QUALITY

        fallback = cfg.routing_model_mid if task in _COMPLEX_TASKS else cfg.routing_model_low
        rules.append(RoutingRule(
            task_type=task.value,
            criteria=criteria,
            min_quality=min_q,
            fallback=fallback,
        ))

    # Catch-all
    rules.append(RoutingRule(
        task_type="*",
        criteria=SelectionCriteria.BEST_VALUE if has_fitness else SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
        min_quality=0.75,
        fallback=cfg.routing_model_mid,
    ))

    policy = RoutingPolicy(rules=rules, version=0)
    validate_policy(policy)
    _cached_bootstrap[cache_key] = policy
    return policy


def default_policy(fitness_cache: Any = None) -> RoutingPolicy:
    """Built-in policy used when no DB or YAML policy is configured.

    Returns the bootstrap policy with conservative routing rules
    instead of an empty passthrough.
    """
    return bootstrap_policy(fitness_cache=fitness_cache)
