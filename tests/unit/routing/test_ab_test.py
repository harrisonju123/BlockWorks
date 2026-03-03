"""Tests for the A/B testing framework.

Verifies deterministic group assignment, split ratio behavior,
and policy selection logic.
"""

from __future__ import annotations

import pytest

from agentproof.routing.ab_test import ABTestConfig, assign_group, get_policy
from agentproof.routing.policy import default_policy
from agentproof.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria


class TestAssignGroup:

    def test_deterministic_assignment(self) -> None:
        """Same trace_id always gets the same group."""
        group1 = assign_group("trace-abc-123", 0.5)
        group2 = assign_group("trace-abc-123", 0.5)
        assert group1 == group2

    def test_different_traces_can_differ(self) -> None:
        """Different trace_ids can land in different groups (statistical, not guaranteed)."""
        groups = {assign_group(f"trace-{i}", 0.5) for i in range(100)}
        # With 100 traces and 50/50 split, both groups should appear
        assert "control" in groups
        assert "experiment" in groups

    def test_split_ratio_zero_all_control(self) -> None:
        """0% experiment means everything goes to control."""
        for i in range(50):
            assert assign_group(f"trace-{i}", 0.0) == "control"

    def test_split_ratio_one_all_experiment(self) -> None:
        """100% experiment means everything goes to experiment."""
        for i in range(50):
            assert assign_group(f"trace-{i}", 1.0) == "experiment"

    def test_split_ratio_respected_at_scale(self) -> None:
        """With enough traces, the split should be approximately correct."""
        n = 10_000
        experiment_count = sum(
            1 for i in range(n) if assign_group(f"trace-{i}", 0.3) == "experiment"
        )
        ratio = experiment_count / n
        # Allow 5% tolerance for hash-based bucketing
        assert 0.25 <= ratio <= 0.35, f"Expected ~0.3, got {ratio}"

    def test_group_values_are_valid(self) -> None:
        group = assign_group("any-trace-id", 0.5)
        assert group in ("control", "experiment")


class TestGetPolicy:

    def _make_policies(self) -> tuple[RoutingPolicy, RoutingPolicy]:
        policy_a = RoutingPolicy(rules=[], version=1)
        policy_b = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ],
            version=2,
        )
        return policy_a, policy_b

    def test_disabled_always_returns_control(self) -> None:
        policy_a, policy_b = self._make_policies()
        config = ABTestConfig(
            policy_a=policy_a,
            policy_b=policy_b,
            split_ratio=0.5,
            enabled=False,
        )

        for i in range(20):
            policy, group = get_policy(f"trace-{i}", config)
            assert group == "control"
            assert policy == policy_a

    def test_enabled_routes_to_both_groups(self) -> None:
        policy_a, policy_b = self._make_policies()
        config = ABTestConfig(
            policy_a=policy_a,
            policy_b=policy_b,
            split_ratio=0.5,
            enabled=True,
        )

        groups_seen: set[str] = set()
        for i in range(100):
            policy, group = get_policy(f"trace-{i}", config)
            groups_seen.add(group)
            if group == "control":
                assert policy == policy_a
            else:
                assert policy == policy_b

        assert "control" in groups_seen
        assert "experiment" in groups_seen

    def test_returns_correct_policy_for_group(self) -> None:
        policy_a, policy_b = self._make_policies()
        config = ABTestConfig(
            policy_a=policy_a,
            policy_b=policy_b,
            split_ratio=0.5,
            enabled=True,
        )

        # Use a known trace_id and verify consistency
        policy, group = get_policy("deterministic-trace", config)
        if group == "control":
            assert policy.version == 1
        else:
            assert policy.version == 2


class TestABTestConfig:

    def test_defaults(self) -> None:
        config = ABTestConfig()
        assert config.split_ratio == 0.5
        assert config.enabled is True
        assert len(config.policy_a.rules) == 0
        assert len(config.policy_b.rules) == 0

    def test_custom_split_ratio(self) -> None:
        config = ABTestConfig(split_ratio=0.2)
        assert config.split_ratio == 0.2
