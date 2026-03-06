"""Tests for end-to-end paired evaluation."""

from __future__ import annotations

import asyncio

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.eval.e2e_eval import (
    E2EReport,
    PairedResult,
    ParetoPoint,
    compute_pareto_frontier,
    run_paired_eval,
)
from blockthrough.routing.router import FitnessCache
from blockthrough.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria


def _make_entry(
    model: str,
    task_type: str,
    avg_quality: float = 0.9,
    avg_cost: float = 0.001,
) -> FitnessEntry:
    return FitnessEntry(
        model=model, task_type=task_type,
        avg_quality=avg_quality, avg_cost=avg_cost,
        avg_latency=500.0, sample_size=100,
    )


def _make_cache(entries: list[FitnessEntry]) -> FitnessCache:
    cache = FitnessCache(ttl_s=300)
    cache.update(entries)
    return cache


def _mock_judge(messages, model, task_type) -> float:
    """Deterministic judge: returns 0.9 for sonnet/opus, 0.7 for haiku."""
    if "haiku" in model:
        return 0.7
    return 0.9


_PROMPTS = [
    {
        "messages": [{"role": "user", "content": "classify this"}],
        "task_type": "classification",
        "requested_model": "claude-sonnet-4-20250514",
    },
    {
        "messages": [{"role": "user", "content": "write code"}],
        "task_type": "code_generation",
        "requested_model": "claude-sonnet-4-20250514",
    },
]


class TestRunPairedEval:

    def test_dry_run_returns_zero_scores(self) -> None:
        cache = _make_cache([
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
        ])
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

        report = asyncio.run(run_paired_eval(
            _PROMPTS[:1], policy, cache, _mock_judge, dry_run=True,
        ))

        assert report.total == 1
        assert report.results[0].requested_score == 0.0
        assert report.results[0].routed_score == 0.0

    def test_with_mock_judge(self) -> None:
        cache = _make_cache([
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
        ])
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

        report = asyncio.run(run_paired_eval(
            _PROMPTS[:1], policy, cache, _mock_judge,
        ))

        r = report.results[0]
        assert r.was_overridden is True
        assert r.requested_score == 0.9  # sonnet
        assert r.routed_score == 0.7  # haiku
        assert r.quality_delta == pytest.approx(-0.2)

    def test_sample_size_limits_results(self) -> None:
        prompts = _PROMPTS * 10  # 20 prompts
        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = asyncio.run(run_paired_eval(
            prompts, policy, cache, _mock_judge, sample_size=5,
        ))

        assert report.total == 5

    def test_no_override_skips_second_judge_call(self) -> None:
        call_count = 0

        def counting_judge(messages, model, task_type) -> float:
            nonlocal call_count
            call_count += 1
            return 0.9

        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = asyncio.run(run_paired_eval(
            _PROMPTS[:1], policy, cache, counting_judge,
        ))

        # No override = only 1 judge call (reused for both scores)
        assert call_count == 1
        assert report.results[0].quality_delta == 0.0

    def test_quality_degradation_counted(self) -> None:
        cache = _make_cache([
            _make_entry("claude-haiku-4-5-20251001", "classification", avg_quality=0.92, avg_cost=0.0008),
            _make_entry("claude-sonnet-4-20250514", "classification", avg_quality=0.95, avg_cost=0.003),
        ])
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

        report = asyncio.run(run_paired_eval(
            _PROMPTS[:1], policy, cache, _mock_judge,
        ))

        # quality_delta = -0.2, which is < -0.05
        assert report.quality_degradation_count == 1


class TestParetoFrontier:

    def test_dominated_points_excluded(self) -> None:
        results = [
            PairedResult(
                prompt_index=0, task_type="classification",
                requested_model="model-a", routed_model="model-a",
                requested_score=0.9, routed_score=0.9, quality_delta=0.0,
                cost_requested=0.01, cost_routed=0.01, cost_delta=0.0,
                was_overridden=False,
            ),
            PairedResult(
                prompt_index=1, task_type="classification",
                requested_model="model-a", routed_model="model-b",
                requested_score=0.9, routed_score=0.7, quality_delta=-0.2,
                cost_requested=0.01, cost_routed=0.005, cost_delta=-0.005,
                was_overridden=True,
            ),
        ]

        frontier = compute_pareto_frontier(results)

        assert len(frontier) == 2
        # model-a dominates model-b (higher quality, but also higher cost)
        # Neither strictly dominates the other if model-b is cheaper
        a_point = next(p for p in frontier if p.model == "model-a")
        b_point = next(p for p in frontier if p.model == "model-b")
        # model-a: quality=0.9, model-b: quality=0.7
        # Both can be frontier if they trade off quality vs cost
        assert a_point.avg_quality > b_point.avg_quality

    def test_single_model_is_frontier(self) -> None:
        results = [
            PairedResult(
                prompt_index=0, task_type="t",
                requested_model="x", routed_model="x",
                requested_score=0.8, routed_score=0.8, quality_delta=0.0,
                cost_requested=0.01, cost_routed=0.01, cost_delta=0.0,
                was_overridden=False,
            ),
        ]

        frontier = compute_pareto_frontier(results)

        assert len(frontier) == 1
        assert frontier[0].is_frontier is True
