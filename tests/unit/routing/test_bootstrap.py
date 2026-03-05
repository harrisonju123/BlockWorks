"""Tests for bootstrap policy, synthetic fitness, and merge logic."""

from __future__ import annotations

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.config import get_config
from blockthrough.models import MODEL_CATALOG
from blockthrough.routing.policy import (
    bootstrap_policy,
    clear_bootstrap_cache,
    validate_policy,
)
from blockthrough.routing.router import (
    FitnessCache,
    _TIER_DEFAULTS,
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
        simple = {"classification", "extraction", "conversation", "tool_selection", "summarization"}
        for rule in policy.rules:
            if rule.task_type in simple:
                assert rule.fallback == cfg.routing_model_low

    def test_hard_tasks_use_mid_fallback(self) -> None:
        cfg = get_config()
        policy = bootstrap_policy()
        hard_tasks = {"code_generation", "code_review", "reasoning"}
        for rule in policy.rules:
            if rule.task_type in hard_tasks:
                assert rule.fallback == cfg.routing_model_mid

    def test_simple_tasks_quality_threshold(self) -> None:
        policy = bootstrap_policy()
        simple = {"classification", "extraction", "conversation", "tool_selection", "summarization"}
        for rule in policy.rules:
            if rule.task_type in simple:
                assert rule.min_quality == 0.55

    def test_hard_tasks_quality_threshold(self) -> None:
        policy = bootstrap_policy()
        hard_tasks = {"code_generation", "code_review", "reasoning"}
        for rule in policy.rules:
            if rule.task_type in hard_tasks:
                assert rule.min_quality == 0.70

    def test_passes_validation(self) -> None:
        policy = bootstrap_policy()
        validate_policy(policy)  # Should not raise

    def test_catch_all_uses_mid_fallback(self) -> None:
        cfg = get_config()
        policy = bootstrap_policy()
        assert policy.rules[-1].fallback == cfg.routing_model_mid


class TestSyntheticFitness:

    def test_generates_entries_for_all_catalog_models(self) -> None:
        entries = generate_synthetic_fitness()
        models_in_entries = {e.model for e in entries}
        expected = {name for name, info in MODEL_CATALOG.items() if info.tier in _TIER_DEFAULTS}
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
        """Should be all catalog models * len(non-unknown task types)."""
        entries = generate_synthetic_fitness()
        n_tasks = len([t for t in TaskType if t != TaskType.UNKNOWN])
        n_models = len([m for m, info in MODEL_CATALOG.items() if info.tier in _TIER_DEFAULTS])
        assert len(entries) == n_models * n_tasks

    def test_task_qualities_override_tier_default(self) -> None:
        """Models with task_qualities get per-task scores, not flat tier defaults."""
        entries = generate_synthetic_fitness()
        # GPT-OSS-120b (tier 2) has reasoning=0.72 which differs from tier-2 default 0.79
        oss_reasoning = next(
            e for e in entries
            if e.model == "openai.gpt-oss-120b-1:0" and e.task_type == "reasoning"
        )
        assert oss_reasoning.avg_quality == 0.72

        # GPT-5.2 (tier 1) has conversation=0.84 which differs from tier-1 default 0.93
        gpt52_conversation = next(
            e for e in entries
            if e.model == "gpt-5.2-chat-latest" and e.task_type == "conversation"
        )
        assert gpt52_conversation.avg_quality == 0.84

    def test_task_qualities_cover_all_task_types(self) -> None:
        """Every model with task_qualities must cover all non-UNKNOWN task types."""
        expected = {t.value for t in TaskType if t != TaskType.UNKNOWN}
        for name, info in MODEL_CATALOG.items():
            if not info.task_qualities:
                continue
            covered = {t for t, _ in info.task_qualities}
            assert covered == expected, (
                f"{name} task_qualities covers {covered}, expected {expected}"
            )


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

    def test_conversation_prefers_cheap_model(self) -> None:
        """For conversation, BEST_VALUE should pick a cheap model with adequate quality."""
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="conversation",
            requested_model="gpt-5.2-chat-latest",
            fitness_cache=cache,
            policy=policy,
        )
        # GPT-OSS-120b has conversation quality 0.81 (above 0.55 threshold)
        # at a fraction of GPT-5.2's cost, so BEST_VALUE should pick it
        selected_info = MODEL_CATALOG.get(decision.selected_model)
        gpt52_info = MODEL_CATALOG["gpt-5.2-chat-latest"]
        assert selected_info is not None
        assert selected_info.avg_cost <= gpt52_info.avg_cost

    def test_reasoning_excludes_budget_models(self) -> None:
        """For reasoning (hard task), budget models with scores <0.70 are excluded."""
        cache = FitnessCache()
        cache.update(generate_synthetic_fitness())
        policy = bootstrap_policy(fitness_cache=cache)

        decision = resolve(
            task_type="reasoning",
            requested_model="claude-opus-4-20250514",
            fitness_cache=cache,
            policy=policy,
        )
        # Budget models have reasoning scores 0.35-0.40, well below the 0.70 hard-task threshold
        assert decision.selected_model != "openai.gpt-oss-20b-1:0"
        assert decision.selected_model != "claude-haiku-4-5-20251001"
        assert decision.selected_model != "gpt-4o-mini"

    def test_complex_task_routes_appropriately(self) -> None:
        """Complex task with opus request should route away from frontier."""
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
        # Should pick a cost-effective model, not opus
        assert decision.selected_model != "claude-opus-4-20250514"

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

    def test_fitness_uses_static_floor_not_median(self) -> None:
        """With fitness data, min_quality uses static floor — not data-derived median."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="classification", model=f"m{i}",
                         avg_quality=q, avg_cost=0.001, avg_latency=500, sample_size=10)
            for i, q in enumerate([0.70, 0.75, 0.78, 0.80, 0.85])
        ])
        policy = bootstrap_policy(fitness_cache=cache)

        classification_rule = next(r for r in policy.rules if r.task_type == "classification")
        # Static floor for simple tasks, NOT the data-derived median (0.78)
        assert classification_rule.min_quality == 0.55

    def test_without_fitness_uses_static(self) -> None:
        """bootstrap_policy(fitness_cache=None) uses CHEAPEST_ABOVE_QUALITY with static thresholds."""
        policy = bootstrap_policy(fitness_cache=None)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY

        simple_rule = next(r for r in policy.rules if r.task_type == "classification")
        assert simple_rule.min_quality == 0.55

        hard_rule = next(r for r in policy.rules if r.task_type == "code_generation")
        assert hard_rule.min_quality == 0.70

    def test_empty_fitness_uses_static(self) -> None:
        """Empty FitnessCache behaves same as None."""
        cache = FitnessCache()
        policy = bootstrap_policy(fitness_cache=cache)

        for rule in policy.rules:
            assert rule.criteria == SelectionCriteria.CHEAPEST_ABOVE_QUALITY

    def test_hard_task_uses_static_floor_with_fitness(self) -> None:
        """Hard tasks use the 0.70 static floor regardless of benchmark data."""
        cache = FitnessCache()
        cache.update([
            FitnessEntry(task_type="code_generation", model=f"m{i}",
                         avg_quality=q, avg_cost=0.001, avg_latency=500, sample_size=20)
            for i, q in enumerate([0.72, 0.745, 0.766, 0.784, 0.787])
        ])
        policy = bootstrap_policy(fitness_cache=cache)

        code_rule = next(r for r in policy.rules if r.task_type == "code_generation")
        # Static floor for hard tasks, NOT data-derived median
        assert code_rule.min_quality == 0.70


