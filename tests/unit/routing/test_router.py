"""Tests for the routing resolution engine.

Covers all three selection criteria, quality floor enforcement,
empty fitness matrix handling, and edge cases.
"""

from __future__ import annotations

import pytest

from agentproof.benchmarking.types import FitnessEntry
from agentproof.routing.router import FitnessCache, resolve
from agentproof.routing.types import (
    QUALITY_FLOOR,
    RoutingPolicy,
    RoutingRule,
    SelectionCriteria,
)


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


class TestResolvePassthrough:

    def test_empty_policy_returns_requested_model(self) -> None:
        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "claude-sonnet-4-20250514"
        assert decision.was_overridden is False
        assert decision.policy_rule_id is None
        assert "passthrough" in decision.reason

    def test_no_matching_rule_returns_requested_model(self) -> None:
        cache = _make_cache([])
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="code_generation",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "claude-sonnet-4-20250514"
        assert decision.was_overridden is False
        assert "no matching rule" in decision.reason


class TestResolveCheapestAboveQuality:

    def test_picks_cheapest_model_above_quality_threshold(self) -> None:
        entries = [
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("gpt-4o-mini", "classification", avg_quality=0.91, avg_cost=0.0004),
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

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "gpt-4o-mini"
        assert decision.was_overridden is True
        assert decision.policy_rule_id == 0

    def test_skips_models_below_min_quality(self) -> None:
        entries = [
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
            _make_entry("gpt-4o-mini", "classification", avg_quality=0.75, avg_cost=0.0004),
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

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        # gpt-4o-mini has quality 0.75 < 0.9, so sonnet should be picked
        assert decision.selected_model == "claude-sonnet-4-20250514"


class TestResolveFastestAboveQuality:

    def test_picks_fastest_model_above_quality_threshold(self) -> None:
        entries = [
            _make_entry("claude-sonnet-4-20250514", "extraction", avg_quality=0.93, avg_latency=800.0),
            _make_entry("gpt-4o-mini", "extraction", avg_quality=0.91, avg_latency=200.0),
            _make_entry("claude-haiku-4-5-20251001", "extraction", avg_quality=0.90, avg_latency=300.0),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="extraction",
                    criteria=SelectionCriteria.FASTEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("extraction", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "gpt-4o-mini"
        assert decision.was_overridden is True

    def test_respects_max_latency_constraint(self) -> None:
        entries = [
            _make_entry("gpt-4o-mini", "extraction", avg_quality=0.91, avg_latency=1500.0),
            _make_entry("claude-haiku-4-5-20251001", "extraction", avg_quality=0.90, avg_latency=300.0),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="extraction",
                    criteria=SelectionCriteria.FASTEST_ABOVE_QUALITY,
                    min_quality=0.9,
                    max_latency_ms=1000.0,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("extraction", "claude-sonnet-4-20250514", cache, policy)

        # gpt-4o-mini is fastest but exceeds max_latency, so haiku is picked
        assert decision.selected_model == "claude-haiku-4-5-20251001"


class TestResolveHighestQualityUnderCost:

    def test_picks_highest_quality_under_cost_cap(self) -> None:
        entries = [
            _make_entry("claude-opus-4-20250514", "code_generation", avg_quality=0.98, avg_cost=0.015),
            _make_entry("claude-sonnet-4-20250514", "code_generation", avg_quality=0.93, avg_cost=0.003),
            _make_entry("claude-haiku-4-5-20251001", "code_generation", avg_quality=0.82, avg_cost=0.0008),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="code_generation",
                    criteria=SelectionCriteria.HIGHEST_QUALITY_UNDER_COST,
                    max_cost_per_1k=0.005,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )

        decision = resolve("code_generation", "claude-opus-4-20250514", cache, policy)

        # Opus exceeds max_cost (0.015 > 0.005), so sonnet is picked (quality 0.93)
        assert decision.selected_model == "claude-sonnet-4-20250514"
        assert decision.was_overridden is True

    def test_all_models_exceed_cost_uses_fallback(self) -> None:
        entries = [
            _make_entry("claude-opus-4-20250514", "code_generation", avg_quality=0.98, avg_cost=0.015),
            _make_entry("claude-sonnet-4-20250514", "code_generation", avg_quality=0.93, avg_cost=0.003),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="code_generation",
                    criteria=SelectionCriteria.HIGHEST_QUALITY_UNDER_COST,
                    max_cost_per_1k=0.001,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("code_generation", "claude-opus-4-20250514", cache, policy)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert "fallback" in decision.reason


class TestQualityFloor:

    def test_quality_floor_overrides_permissive_min_quality(self) -> None:
        """Even if policy allows min_quality=0.5, the global floor of 0.7 applies."""
        entries = [
            _make_entry("cheap-model", "classification", avg_quality=0.6, avg_cost=0.0001),
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.85, avg_cost=0.0008),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.5,  # Permissive, but floor is 0.7
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        # cheap-model has quality 0.6 < QUALITY_FLOOR (0.7), so it's excluded
        assert decision.selected_model == "claude-haiku-4-5-20251001"

    def test_quality_floor_constant_is_correct(self) -> None:
        assert QUALITY_FLOOR == 0.7

    def test_model_exactly_at_floor_is_included(self) -> None:
        entries = [
            _make_entry("border-model", "classification", avg_quality=0.7, avg_cost=0.0001),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.7,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "border-model"


class TestCatchAllRule:

    def test_catch_all_matches_any_task_type(self) -> None:
        entries = [
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

        decision = resolve("conversation", "claude-opus-4-20250514", cache, policy)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is True
        assert decision.policy_rule_id == 0

    def test_specific_rule_takes_precedence_over_catch_all(self) -> None:
        entries = [
            _make_entry("gpt-4o-mini", "classification", avg_quality=0.92, avg_cost=0.0004),
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.85, avg_cost=0.0008),
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
                RoutingRule(
                    task_type="*",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.8,
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )

        decision = resolve("classification", "claude-opus-4-20250514", cache, policy)

        # Classification rule (index 0) matches first, min_quality=0.9
        # gpt-4o-mini has quality 0.92 >= 0.9 and is cheapest
        assert decision.selected_model == "gpt-4o-mini"
        assert decision.policy_rule_id == 0


class TestEdgeCases:

    def test_empty_fitness_matrix_uses_fallback(self) -> None:
        cache = _make_cache([])
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert "fitness matrix empty" in decision.reason

    def test_unknown_task_type_no_catch_all_passthrough(self) -> None:
        cache = _make_cache([])
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("unknown", "claude-opus-4-20250514", cache, policy)

        assert decision.selected_model == "claude-opus-4-20250514"
        assert decision.was_overridden is False

    def test_no_models_meet_criteria_uses_fallback(self) -> None:
        """All models have quality below the effective threshold."""
        entries = [
            _make_entry("model-a", "classification", avg_quality=0.5, avg_cost=0.001),
            _make_entry("model-b", "classification", avg_quality=0.6, avg_cost=0.002),
        ]
        cache = _make_cache(entries)
        policy = RoutingPolicy(
            rules=[
                RoutingRule(
                    task_type="classification",
                    criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                    min_quality=0.95,
                    fallback="claude-haiku-4-5-20251001",
                ),
            ]
        )

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert "fallback" in decision.reason

    def test_requested_model_returned_when_it_wins(self) -> None:
        """When the requested model is also the best candidate, was_overridden=False."""
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
                    fallback="claude-sonnet-4-20250514",
                ),
            ]
        )

        decision = resolve("classification", "claude-haiku-4-5-20251001", cache, policy)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is False


class TestToolUseFiltering:

    def test_tool_use_excludes_unsupported_models(self) -> None:
        """Models with supports_tool_use=False are excluded when has_tool_use=True."""
        entries = [
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.93, avg_cost=0.003),
            # Bedrock-backed model — supports_tool_use=False in MODEL_CATALOG
            _make_entry("openai.gpt-oss-120b-1:0", "classification", avg_quality=0.92, avg_cost=0.00015),
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

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy, has_tool_use=True)

        # gpt-oss is cheapest but doesn't support tools — sonnet should win
        assert decision.selected_model == "claude-sonnet-4-20250514"

    def test_no_tool_use_keeps_unsupported_models(self) -> None:
        """Without tool use, Bedrock-backed models are still valid candidates."""
        entries = [
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.93, avg_cost=0.003),
            _make_entry("openai.gpt-oss-120b-1:0", "classification", avg_quality=0.92, avg_cost=0.00015),
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

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy, has_tool_use=False)

        assert decision.selected_model == "openai.gpt-oss-120b-1:0"

    def test_tool_use_all_filtered_falls_through_to_fallback(self) -> None:
        """When all candidates lack tool support, fallback is used."""
        entries = [
            _make_entry("openai.gpt-oss-120b-1:0", "classification", avg_quality=0.92, avg_cost=0.00015),
            _make_entry("google.gemma-3-27b-it", "classification", avg_quality=0.91, avg_cost=0.00004),
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

        decision = resolve("classification", "claude-sonnet-4-20250514", cache, policy, has_tool_use=True)

        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert "fallback" in decision.reason


class TestFitnessCache:

    def test_cache_starts_empty(self) -> None:
        cache = FitnessCache()
        assert cache.is_empty is True
        assert cache.is_stale is True

    def test_update_populates_cache(self) -> None:
        cache = FitnessCache(ttl_s=300)
        entries = [
            _make_entry("model-a", "classification"),
            _make_entry("model-b", "classification"),
        ]
        cache.update(entries)

        assert cache.is_empty is False
        assert cache.is_stale is False
        assert len(cache.get_entries_for_task("classification")) == 2

    def test_stale_after_ttl(self) -> None:
        cache = FitnessCache(ttl_s=0)  # Immediately stale
        cache.update([_make_entry("model-a", "classification")])
        assert cache.is_stale is True

    def test_entries_indexed_by_task_type(self) -> None:
        cache = FitnessCache()
        entries = [
            _make_entry("model-a", "classification"),
            _make_entry("model-b", "code_generation"),
            _make_entry("model-c", "classification"),
        ]
        cache.update(entries)

        assert len(cache.get_entries_for_task("classification")) == 2
        assert len(cache.get_entries_for_task("code_generation")) == 1
        assert len(cache.get_entries_for_task("summarization")) == 0

    def test_entries_sorted_by_quality_descending(self) -> None:
        cache = FitnessCache()
        entries = [
            _make_entry("low-quality", "classification", avg_quality=0.7),
            _make_entry("high-quality", "classification", avg_quality=0.95),
            _make_entry("mid-quality", "classification", avg_quality=0.85),
        ]
        cache.update(entries)

        result = cache.get_entries_for_task("classification")
        assert result[0].model == "high-quality"
        assert result[1].model == "mid-quality"
        assert result[2].model == "low-quality"
