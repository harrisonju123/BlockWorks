"""Tests for bootstrap policy, synthetic fitness, and merge logic."""

from __future__ import annotations

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.config import get_config
from blockthrough.models import MODEL_CATALOG
from blockthrough.routing.policy import (
    _compute_task_threshold,
    bootstrap_policy,
    clear_bootstrap_cache,
    validate_policy,
)
from blockthrough.routing.router import (
    FitnessCache,
    _TIER_DEFAULTS,
    _get_tier_models,
    generate_synthetic_fitness,
    merge_fitness_entries,
    resolve,
)
from blockthrough.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria
from blockthrough.types import TaskType


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear config and bootstrap caches between tests."""
    get_config.cache_clear()
    clear_bootstrap_cache()
    yield
    get_config.cache_clear()
    clear_bootstrap_cache()


class TestBootstrapPolicy:

    def test_has_rules(self) -> None:
        policy = bootstrap_policy()
        assert len(policy.rules) > 0

    def test_version_zero(self) -> None:
        policy = bootstrap_policy()
        assert policy.version == 0

    def test_covers_all_non_unknown_task_types(self) -> None:
        """Every TaskType except UNKNOWN should have a dedicated rule."""
        policy = bootstrap_policy()
        covered = {r.task_type for r in policy.rules if not r.is_catch_all}
        expected = {t.value for t in TaskType if t != TaskType.UNKNOWN}
        assert covered == expected

    def test_catch_all_is_last(self) -> None:
        policy = bootstrap_policy()
        assert policy.rules[-1].is_catch_all
        for rule in policy.rules[:-1]:
            assert not rule.is_catch_all

    def test_simple_tasks_use_low_fallback(self) -> None:
        cfg = get_config()
        policy = bootstrap_policy()
        simple = {"classification", "extraction", "conversation", "tool_selection"}
        for rule in policy.rules:
            if rule.task_type in simple:
                assert rule.fallback == cfg.routing_model_low

    def test_complex_tasks_use_mid_fallback(self) -> None:
        cfg = get_config()
        policy = bootstrap_policy()
        complex_tasks = {"code_generation", "code_review", "summarization", "reasoning"}
        for rule in policy.rules:
            if rule.task_type in complex_tasks:
                assert rule.fallback == cfg.routing_model_mid

    def test_simple_tasks_quality_threshold(self) -> None:
        policy = bootstrap_policy()
        simple = {"classification", "extraction", "conversation", "tool_selection"}
        for rule in policy.rules:
            if rule.task_type in simple:
                assert rule.min_quality == 0.8

    def test_complex_tasks_quality_threshold(self) -> None:
        policy = bootstrap_policy()
        complex_tasks = {"code_generation", "code_review", "summarization", "reasoning"}
        for rule in policy.rules:
            if rule.task_type in complex_tasks:
                assert rule.min_quality == 0.85

    def test_passes_validation(self) -> None:
        policy = bootstrap_policy()
        validate_policy(policy)  # Should not raise

    def test_catch_all_uses_mid_fallback(self) -> None:
        cfg = get_config()
        policy = bootstrap_policy()
        assert policy.rules[-1].fallback == cfg.routing_model_mid


class TestTierModels:

    def test_returns_3_models(self) -> None:
        tier_models = _get_tier_models()
        assert len(tier_models) == 3
        assert set(tier_models.keys()) == {1, 2, 3}

    def test_defaults_match_config(self) -> None:
        cfg = get_config()
        tier_models = _get_tier_models()
        assert tier_models[1] == cfg.routing_model_high
        assert tier_models[2] == cfg.routing_model_mid
        assert tier_models[3] == cfg.routing_model_low

    def test_correct_quality_per_tier(self) -> None:
        entries = generate_synthetic_fitness()
        tier_models = _get_tier_models()
        tier_by_model = {model: tier for tier, model in tier_models.items()}
        for entry in entries:
            tier = tier_by_model[entry.model]
            expected_quality = _TIER_DEFAULTS[tier]["quality"]
            assert entry.avg_quality == expected_quality


class TestSyntheticFitness:

    def test_generates_entries_for_tier_models_only(self) -> None:
        entries = generate_synthetic_fitness()
        models_in_entries = {e.model for e in entries}
        cfg = get_config()
        expected = {cfg.routing_model_high, cfg.routing_model_mid, cfg.routing_model_low}
        assert models_in_entries == expected

    def test_excludes_unknown_task_type(self) -> None:
        entries = generate_synthetic_fitness()
        task_types_in_entries = {e.task_type for e in entries}
        assert TaskType.UNKNOWN.value not in task_types_in_entries

    def test_covers_all_non_unknown_task_types(self) -> None:
        entries = generate_synthetic_fitness()
        task_types_in_entries = {e.task_type for e in entries}
        expected = {t.value for t in TaskType if t != TaskType.UNKNOWN}
        assert task_types_in_entries == expected

    def test_cost_matches_catalog(self) -> None:
        entries = generate_synthetic_fitness()
        for entry in entries:
            info = MODEL_CATALOG[entry.model]
            assert entry.avg_cost == pytest.approx(info.avg_cost)

    def test_sample_size_zero_sentinel(self) -> None:
        """sample_size=0 marks entries as synthetic."""
        entries = generate_synthetic_fitness()
        assert all(e.sample_size == 0 for e in entries)

    def test_entry_count(self) -> None:
        """Should be 3 tier models * len(non-unknown task types)."""
        entries = generate_synthetic_fitness()
        n_tasks = len([t for t in TaskType if t != TaskType.UNKNOWN])
        assert len(entries) == 3 * n_tasks


class TestMergeFitness:

    def test_real_overwrites_quality_and_latency(self) -> None:
        synthetic = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.85, avg_cost=0.009, avg_latency=1000.0, sample_size=0,
            ),
        ]
        real = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.92, avg_cost=999.0, avg_latency=900.0, sample_size=50,
            ),
        ]
        merged = merge_fitness_entries(synthetic, real)
        assert len(merged) == 1
        assert merged[0].avg_quality == 0.92
        assert merged[0].avg_latency == 900.0
        assert merged[0].sample_size == 50

    def test_real_cost_normalized_to_catalog(self) -> None:
        """DB benchmark_cost is per-request; merge normalizes to catalog pricing."""
        synthetic = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.85, avg_cost=0.009, avg_latency=1000.0, sample_size=0,
            ),
        ]
        real = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.92, avg_cost=24.44, avg_latency=900.0, sample_size=50,
            ),
        ]
        merged = merge_fitness_entries(synthetic, real)
        # Cost comes from MODEL_CATALOG, not the raw DB value
        catalog_cost = MODEL_CATALOG["claude-sonnet-4-6"].avg_cost
        assert merged[0].avg_cost == pytest.approx(catalog_cost)

    def test_new_models_added_from_real(self) -> None:
        """Real entries for new models (discovered via benchmarking) are included."""
        synthetic = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.85, avg_cost=0.009, avg_latency=1000.0, sample_size=0,
            ),
        ]
        real = [
            FitnessEntry(
                task_type="classification", model="gpt-4o-mini",
                avg_quality=0.80, avg_cost=5.0, avg_latency=800.0, sample_size=25,
            ),
        ]
        merged = merge_fitness_entries(synthetic, real)
        assert len(merged) == 2
        models = {e.model for e in merged}
        assert models == {"claude-sonnet-4-6", "gpt-4o-mini"}
        # New model's cost normalized to catalog pricing
        gpt_entry = next(e for e in merged if e.model == "gpt-4o-mini")
        catalog_cost = MODEL_CATALOG["gpt-4o-mini"].avg_cost
        assert gpt_entry.avg_cost == pytest.approx(catalog_cost)

    def test_empty_real_preserves_synthetic(self) -> None:
        synthetic = generate_synthetic_fitness()
        merged = merge_fitness_entries(synthetic, [])
        assert len(merged) == len(synthetic)

    def test_zero_sample_real_not_merged(self) -> None:
        """Real entries with sample_size=0 don't overwrite synthetic."""
        synthetic = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.85, avg_cost=0.009, avg_latency=1000.0, sample_size=0,
            ),
        ]
        real = [
            FitnessEntry(
                task_type="classification", model="claude-sonnet-4-6",
                avg_quality=0.50, avg_cost=0.009, avg_latency=1000.0, sample_size=0,
            ),
        ]
        merged = merge_fitness_entries(synthetic, real)
        assert merged[0].avg_quality == 0.85


class TestBootstrapWithSyntheticFitness:
    """Integration: bootstrap policy + synthetic cache -> resolve() actually routes."""

    def test_opus_request_gets_rerouted(self) -> None:
        """An opus request for a simple task should be routed to a better-value model."""
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="classification",
            requested_model="claude-opus-4-20250514",
            fitness_cache=cache,
            policy=policy,
        )
        assert decision.was_overridden
        assert decision.selected_model != "claude-opus-4-20250514"

    def test_haiku_request_rerouted_below_quality_floor(self) -> None:
        """Haiku's synthetic quality (0.75) is below min_quality (0.8), so it gets rerouted."""
        cfg = get_config()
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="classification",
            requested_model="claude-haiku-4-5-20251001",
            fitness_cache=cache,
            policy=policy,
        )
        # Tier-3 synthetic quality (0.75) < min_quality (0.8), so router
        # picks the mid model as best value above quality threshold
        assert decision.was_overridden
        assert decision.selected_model == cfg.routing_model_mid

    def test_complex_task_routes_appropriately(self) -> None:
        """Complex task with opus request should route to mid (best value above threshold)."""
        cfg = get_config()
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="code_generation",
            requested_model="claude-opus-4-20250514",
            fitness_cache=cache,
            policy=policy,
        )
        assert decision.was_overridden
        # Both high and mid meet threshold; mid has better quality/cost ratio
        assert decision.selected_model == cfg.routing_model_mid

    def test_catch_all_handles_unknown_task(self) -> None:
        """Unknown task type falls through to catch-all rule."""
        cfg = get_config()
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="unknown",
            requested_model="claude-opus-4-20250514",
            fitness_cache=cache,
            policy=policy,
        )
        # Catch-all matches, but no fitness entries for "unknown" -> uses fallback
        assert decision.selected_model == cfg.routing_model_mid
        assert decision.policy_rule_id is not None

    def test_fitness_cache_produces_best_value_criteria(self) -> None:
        """When fitness_cache is provided, bootstrap uses BEST_VALUE criteria."""
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.BEST_VALUE


class TestBestValueCriteria:
    """Test BEST_VALUE selection in the router."""

    def test_picks_highest_quality_cost_ratio(self) -> None:
        """BEST_VALUE picks the candidate with the best quality/cost ratio."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model="model-a",
                         avg_quality=0.85, avg_cost=0.001, avg_latency=500, sample_size=10),
            FitnessEntry(task_type="classification", model="model-b",
                         avg_quality=0.90, avg_cost=0.05, avg_latency=500, sample_size=10),
            FitnessEntry(task_type="classification", model="model-c",
                         avg_quality=0.83, avg_cost=0.0001, avg_latency=500, sample_size=10),
        ])
        rule = RoutingRule(
            task_type="classification",
            criteria=SelectionCriteria.BEST_VALUE,
            min_quality=0.84,
            fallback="model-b",
        )
        policy = RoutingPolicy(rules=[rule])

        decision = resolve("classification", "model-b", cache, policy)
        # model-c excluded (0.83 < 0.84). model-a ratio=850, model-b ratio=18.
        assert decision.selected_model == "model-a"

    def test_respects_min_quality_floor(self) -> None:
        """Candidate with best ratio but below min_quality is excluded."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model="cheap-bad",
                         avg_quality=0.72, avg_cost=0.00001, avg_latency=100, sample_size=10),
            FitnessEntry(task_type="classification", model="decent",
                         avg_quality=0.85, avg_cost=0.01, avg_latency=500, sample_size=10),
        ])
        rule = RoutingRule(
            task_type="classification",
            criteria=SelectionCriteria.BEST_VALUE,
            min_quality=0.8,
            fallback="decent",
        )
        policy = RoutingPolicy(rules=[rule])

        decision = resolve("classification", "decent", cache, policy)
        # cheap-bad has amazing ratio but quality 0.72 < 0.8 threshold
        assert decision.selected_model == "decent"

    def test_respects_hard_constraints(self) -> None:
        """Model with best ratio but above max_cost_per_1k is skipped."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model="expensive-good",
                         avg_quality=0.95, avg_cost=0.05, avg_latency=500, sample_size=10),
            FitnessEntry(task_type="classification", model="cheap-ok",
                         avg_quality=0.85, avg_cost=0.001, avg_latency=500, sample_size=10),
        ])
        rule = RoutingRule(
            task_type="classification",
            criteria=SelectionCriteria.BEST_VALUE,
            min_quality=0.8,
            max_cost_per_1k=0.01,
            fallback="cheap-ok",
        )
        policy = RoutingPolicy(rules=[rule])

        decision = resolve("classification", "cheap-ok", cache, policy)
        # expensive-good exceeds max_cost_per_1k, so cheap-ok is selected
        assert decision.selected_model == "cheap-ok"


class TestDataDrivenBootstrap:
    """Test bootstrap_policy with real fitness data."""

    def test_with_fitness_uses_best_value(self) -> None:
        """Populated FitnessCache produces rules with BEST_VALUE criteria."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model="m1",
                         avg_quality=0.85, avg_cost=0.001, avg_latency=500, sample_size=20),
            FitnessEntry(task_type="classification", model="m2",
                         avg_quality=0.80, avg_cost=0.0005, avg_latency=300, sample_size=15),
        ])
        policy = bootstrap_policy(fitness_cache=cache)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.BEST_VALUE

    def test_threshold_from_median(self) -> None:
        """Threshold is derived from median quality of benchmarked models."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model=f"m{i}",
                         avg_quality=q, avg_cost=0.001, avg_latency=500, sample_size=10)
            for i, q in enumerate([0.70, 0.75, 0.78, 0.80, 0.85])
        ])
        policy = bootstrap_policy(fitness_cache=cache)

        classification_rule = next(r for r in policy.rules if r.task_type == "classification")
        # median of [0.70, 0.75, 0.78, 0.80, 0.85] = 0.78
        assert classification_rule.min_quality == 0.78

    def test_without_fitness_uses_static(self) -> None:
        """bootstrap_policy(fitness_cache=None) uses CHEAPEST_ABOVE_QUALITY with static thresholds."""
        policy = bootstrap_policy(fitness_cache=None)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY

        simple_rule = next(r for r in policy.rules if r.task_type == "classification")
        assert simple_rule.min_quality == 0.8

        complex_rule = next(r for r in policy.rules if r.task_type == "code_generation")
        assert complex_rule.min_quality == 0.85

    def test_empty_fitness_uses_static(self) -> None:
        """Empty FitnessCache behaves same as None."""
        cache = FitnessCache()
        policy = bootstrap_policy(fitness_cache=cache)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY

    def test_complex_tasks_no_longer_unreachable(self) -> None:
        """With real benchmark data, code_generation threshold drops below the quality ceiling."""
        cache = FitnessCache()
        # Simulate real benchmark data where quality ceiling is ~0.787
        cache.update([
            FitnessEntry(task_type="code_generation", model=f"m{i}",
                         avg_quality=q, avg_cost=0.001, avg_latency=500, sample_size=20)
            for i, q in enumerate([0.72, 0.745, 0.766, 0.784, 0.787])
        ])
        policy = bootstrap_policy(fitness_cache=cache)

        code_rule = next(r for r in policy.rules if r.task_type == "code_generation")
        # median = 0.766, much lower than the old hardcoded 0.85
        assert code_rule.min_quality == 0.766
        # Multiple models now qualify (those at 0.766 and above)
        assert code_rule.min_quality < 0.787


class TestComputeTaskThreshold:

    def test_median_of_real_entries(self) -> None:
        entries = [
            FitnessEntry(task_type="x", model=f"m{i}",
                         avg_quality=q, avg_cost=0.001, avg_latency=500, sample_size=10)
            for i, q in enumerate([0.70, 0.80, 0.85, 0.90])
        ]
        # median of [0.70, 0.80, 0.85, 0.90] -> index 2 -> 0.85
        assert _compute_task_threshold(entries) == 0.85

    def test_ignores_synthetic_entries(self) -> None:
        """Entries with sample_size=0 are filtered out."""
        entries = [
            FitnessEntry(task_type="x", model="syn",
                         avg_quality=0.95, avg_cost=0.001, avg_latency=500, sample_size=0),
            FitnessEntry(task_type="x", model="real",
                         avg_quality=0.78, avg_cost=0.001, avg_latency=500, sample_size=10),
        ]
        # Only the real entry (0.78) counts
        assert _compute_task_threshold(entries) == 0.78

    def test_clamps_to_quality_floor(self) -> None:
        """Threshold never goes below QUALITY_FLOOR (0.7)."""
        entries = [
            FitnessEntry(task_type="x", model="bad",
                         avg_quality=0.5, avg_cost=0.001, avg_latency=500, sample_size=10),
        ]
        assert _compute_task_threshold(entries) == 0.7

    def test_empty_returns_default(self) -> None:
        assert _compute_task_threshold([]) == 0.8
