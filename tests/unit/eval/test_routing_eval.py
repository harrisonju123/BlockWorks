"""Tests for routing policy simulation and comparison."""

from __future__ import annotations

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.eval.routing_eval import compare_policies, simulate_policy, simulate_with_defaults
from blockthrough.eval.types import ExpectedBehavior, RoutingExpectation
from blockthrough.routing.router import FitnessCache
from blockthrough.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria


def _make_entry(
    model: str,
    task_type: str,
    avg_quality: float = 0.9,
    avg_cost: float = 0.001,
    avg_latency: float = 500.0,
    sample_size: int = 100,
) -> FitnessEntry:
    return FitnessEntry(
        model=model,
        task_type=task_type,
        avg_quality=avg_quality,
        avg_cost=avg_cost,
        avg_latency=avg_latency,
        sample_size=sample_size,
    )


def _make_cache(entries: list[FitnessEntry]) -> FitnessCache:
    cache = FitnessCache(ttl_s=300)
    cache.update(entries)
    return cache


class TestSimulatePolicy:

    def test_empty_policy_all_passthrough(self) -> None:
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-sonnet-4-20250514",
                expected_behavior=ExpectedBehavior.PASSTHROUGH,
            ),
            RoutingExpectation(
                task_type="code_generation",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.PASSTHROUGH,
            ),
        ]
        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = simulate_policy(expectations, policy, cache)

        assert report.total == 2
        assert report.behavior_accuracy == 1.0
        assert report.override_rate == 0.0

    def test_override_detected(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-sonnet-4-20250514",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        assert report.override_rate == 1.0
        assert report.behavior_accuracy == 1.0
        assert report.rows[0].decision_model == "claude-haiku-4-5-20251001"

    def test_behavior_accuracy_with_known_outcomes(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-opus-4-6-20250527", "code_generation", avg_quality=0.98, avg_cost=0.015),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
                description="Opus downgraded to Haiku for classification",
            ),
            RoutingExpectation(
                task_type="code_generation",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.PASSTHROUGH,
                description="No rule for code_gen, passthrough",
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        assert report.behavior_accuracy == 1.0

    def test_cost_delta_negative_on_downgrade(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-opus-4-6-20250527", "classification", avg_quality=0.96, avg_cost=0.015),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        assert report.avg_cost_delta < 0, "Downgrading should produce negative cost delta"

    def test_quality_risk_counted(self) -> None:
        """Override to a model with much lower quality should be flagged."""
        entries = [
            _make_entry("cheap-model", "classification", avg_quality=0.50, avg_cost=0.0001),
            _make_entry("claude-opus-4-6-20250527", "classification", avg_quality=0.96, avg_cost=0.015),
        ]
        cache = _make_cache(entries)
        # Permissive policy that allows low-quality routing
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.3,
                    fallback="cheap-model",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        assert report.quality_risk_count == 1

    def test_per_task_breakdown_sums_to_total(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-haiku-4-5-20251001", "conversation", avg_quality=0.88, avg_cost=0.0008),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="*",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.8,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
            RoutingExpectation(
                task_type="conversation",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-haiku-4-5-20251001",
                expected_behavior=ExpectedBehavior.PASSTHROUGH,
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        breakdown_total = sum(bd.total for bd in report.per_task_breakdown.values())
        assert breakdown_total == report.total

    def test_exact_model_match(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
                expected_model="claude-haiku-4-5-20251001",
            ),
        ]

        report = simulate_policy(expectations, policy, cache)

        assert report.rows[0].model_match is True


class TestComparePolicies:

    def test_identical_policies_full_agreement(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
        ]

        comparison = compare_policies(expectations, policy, policy, cache)

        assert comparison.behavior_agreement_rate == 1.0
        assert len(comparison.differing_decisions) == 0

    def test_different_policies_detects_diff(self) -> None:
        entries = [
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.85, avg_cost=0.0008),
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
        ]
        cache = _make_cache(entries)

        # Policy A: strict quality threshold — Haiku doesn't qualify
        policy_a = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )
        # Policy B: relaxed threshold — Haiku qualifies
        policy_b = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.8,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )
        expectations = [
            RoutingExpectation(
                task_type="classification",
                requested_model="claude-opus-4-6-20250527",
                expected_behavior=ExpectedBehavior.OVERRIDE,
            ),
        ]

        comparison = compare_policies(expectations, policy_a, policy_b, cache)

        assert len(comparison.differing_decisions) == 1
        row_a, row_b = comparison.differing_decisions[0]
        assert row_a.decision_model == "claude-sonnet-4-20250514"
        assert row_b.decision_model == "claude-haiku-4-5-20251001"


class TestSimulateWithDefaults:
    """Test simulate_with_defaults() against the built-in expectations fixture."""

    @classmethod
    def setup_class(cls) -> None:
        import os
        from unittest.mock import patch

        from blockthrough.eval.routing_eval import load_expectations

        env_patch = patch.dict(os.environ, {"AGENTPROOF_DB_URL": "postgresql://x:x@localhost/x"})
        env_patch.start()
        cls._env_patch = env_patch

        # Clear config cache so the patched env var is picked up
        from blockthrough.config import get_config
        get_config.cache_clear()

        cls.expectations = load_expectations()
        cls.report = simulate_with_defaults(cls.expectations)

    @classmethod
    def teardown_class(cls) -> None:
        cls._env_patch.stop()
        from blockthrough.config import get_config
        get_config.cache_clear()

    def test_fixture_loads(self) -> None:
        assert len(self.expectations) >= 25

    def test_behavior_accuracy_above_80_percent(self) -> None:
        assert self.report.behavior_accuracy >= 0.80, (
            f"Behavior accuracy {self.report.behavior_accuracy:.1%} below 80% target"
        )

    def test_all_task_types_covered(self) -> None:
        task_types = {e.task_type for e in self.expectations}
        for required in ("classification", "code_generation", "code_review",
                         "reasoning", "summarization", "extraction",
                         "conversation", "tool_selection", "unknown"):
            assert required in task_types, f"Missing task type '{required}' in fixture"

    def test_tool_use_scenarios_present(self) -> None:
        tool_use_exps = [e for e in self.expectations if e.has_tool_use]
        assert len(tool_use_exps) >= 3, "Need at least 3 tool-use test scenarios"
