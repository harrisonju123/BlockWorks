"""Tests for routing policy parsing, validation, and the default policy."""

from __future__ import annotations

import pytest

from blockthrough.routing.policy import (
    PolicyValidationError,
    default_policy,
    load_policy,
    validate_policy,
)
from blockthrough.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria


class TestLoadPolicy:

    def test_load_from_dict(self) -> None:
        raw = {
            "rules": [
                {
                    "task_type": "classification",
                    "criteria": "cheapest_above_quality",
                    "min_quality": 0.9,
                    "fallback": "claude-haiku-4-5-20251001",
                }
            ],
            "version": 1,
        }
        policy = load_policy(raw)
        assert len(policy.rules) == 1
        assert policy.rules[0].task_type == "classification"
        assert policy.rules[0].criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY
        assert policy.rules[0].min_quality == 0.9
        assert policy.rules[0].fallback == "claude-haiku-4-5-20251001"

    def test_load_from_yaml_string(self) -> None:
        yaml_str = """
rules:
  - task_type: code_generation
    criteria: highest_quality_under_cost
    max_cost_per_1k: 0.02
    fallback: claude-sonnet-4-20250514
  - task_type: "*"
    criteria: cheapest_above_quality
    min_quality: 0.8
    fallback: claude-sonnet-4-20250514
"""
        policy = load_policy(yaml_str)
        assert len(policy.rules) == 2
        assert policy.rules[0].task_type == "code_generation"
        assert policy.rules[0].max_cost_per_1k == 0.02
        assert policy.rules[1].is_catch_all

    def test_load_empty_dict_returns_empty_policy(self) -> None:
        policy = load_policy({})
        assert len(policy.rules) == 0

    def test_load_with_all_criteria_types(self) -> None:
        raw = {
            "rules": [
                {
                    "task_type": "classification",
                    "criteria": "cheapest_above_quality",
                    "fallback": "claude-haiku-4-5-20251001",
                },
                {
                    "task_type": "extraction",
                    "criteria": "fastest_above_quality",
                    "fallback": "gpt-4o-mini",
                },
                {
                    "task_type": "code_generation",
                    "criteria": "highest_quality_under_cost",
                    "fallback": "claude-sonnet-4-20250514",
                },
            ],
        }
        policy = load_policy(raw)
        assert policy.rules[0].criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY
        assert policy.rules[1].criteria == SelectionCriteria.FASTEST_ABOVE_QUALITY
        assert policy.rules[2].criteria == SelectionCriteria.HIGHEST_QUALITY_UNDER_COST


class TestValidatePolicy:

    def test_valid_policy_passes(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
                RoutingRule(
                    task_type="*",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.8,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )
        # Should not raise
        validate_policy(policy)

    def test_unknown_task_type_rejected(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="telepathy",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        assert "unknown task_type 'telepathy'" in str(exc_info.value)

    def test_unknown_fallback_model_rejected(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="nonexistent-model-v99",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        assert "fallback model 'nonexistent-model-v99'" in str(exc_info.value)

    def test_duplicate_task_type_rejected(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.FASTEST_ABOVE_QUALITY,
                    fallback="gpt-4o-mini",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        assert "duplicate task_type 'classification'" in str(exc_info.value)

    def test_rules_after_catch_all_rejected(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="*",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        assert "after catch-all" in str(exc_info.value)

    def test_negative_max_cost_rejected(self) -> None:
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    max_cost_per_1k=-0.01,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        assert "max_cost_per_1k must be positive" in str(exc_info.value)

    def test_empty_policy_is_valid(self) -> None:
        policy = RoutingPolicy(rules=[])
        validate_policy(policy)  # Should not raise

    def test_multiple_errors_collected(self) -> None:
        """All validation errors are collected, not just the first one."""
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="telepathy",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="nonexistent-model",
                ),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy(policy)
        # Both the task_type and fallback errors should be present
        assert len(exc_info.value.errors) >= 2


class TestDefaultPolicy:

    def test_default_policy_has_bootstrap_rules(self) -> None:
        policy = default_policy()
        assert len(policy.rules) > 0
        assert policy.version == 0

    def test_default_policy_is_valid(self) -> None:
        policy = default_policy()
        validate_policy(policy)  # Should not raise
