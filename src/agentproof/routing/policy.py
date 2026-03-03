"""YAML-based routing policy loader and validator.

The policy DSL lets operators define per-task-type model selection rules
without touching code. Validation catches misconfiguration at load time
rather than at request time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentproof.models import MODEL_CATALOG
from agentproof.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria
from agentproof.types import TaskType

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


def default_policy() -> RoutingPolicy:
    """Built-in passthrough policy that returns the originally requested model.

    An empty rule list means resolve() will always return the requested model
    unchanged -- no routing decisions are made.
    """
    return RoutingPolicy(rules=[], version=0)
