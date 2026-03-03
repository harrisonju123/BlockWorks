"""Tests for the dry-run simulator.

Uses mock DB sessions to verify that the dry-run correctly replays
historical events through the router and aggregates results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentproof.benchmarking.types import FitnessEntry
from agentproof.routing.dry_run import DryRunReport, _estimate_routed_cost, dry_run
from agentproof.routing.router import FitnessCache
from agentproof.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria


def _mock_row(**kwargs) -> MagicMock:
    row = MagicMock()
    row._mapping = kwargs
    return row


def _make_cache(entries: list[FitnessEntry]) -> FitnessCache:
    cache = FitnessCache(ttl_s=300)
    cache.update(entries)
    return cache


class TestEstimateRoutedCost:

    def test_known_models_use_cost_ratio(self) -> None:
        # Opus avg_cost = (0.015 + 0.075) / 2 = 0.045
        # Haiku avg_cost = (0.0008 + 0.004) / 2 = 0.0024
        # ratio = 0.0024 / 0.045 = 0.05333...
        cost = _estimate_routed_cost(
            "claude-opus-4-20250514", 1.0, "claude-haiku-4-5-20251001"
        )
        assert cost == pytest.approx(0.0024 / 0.045, rel=1e-3)

    def test_unknown_original_model_returns_same_cost(self) -> None:
        cost = _estimate_routed_cost("unknown-model", 5.0, "claude-haiku-4-5-20251001")
        assert cost == 5.0

    def test_unknown_routed_model_returns_same_cost(self) -> None:
        cost = _estimate_routed_cost("claude-opus-4-20250514", 5.0, "unknown-model")
        assert cost == 5.0

    def test_same_model_returns_same_cost(self) -> None:
        cost = _estimate_routed_cost(
            "claude-sonnet-4-20250514", 3.0, "claude-sonnet-4-20250514"
        )
        assert cost == pytest.approx(3.0)


class TestDryRun:

    @pytest.mark.asyncio
    async def test_empty_events_returns_zero_report(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        session = AsyncMock()
        session.execute.return_value = mock_result

        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = await dry_run(
            policy=policy,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session=session,
            fitness_cache=cache,
        )

        assert isinstance(report, DryRunReport)
        assert report.total_events == 0
        assert report.events_affected == 0
        assert report.cost_savings == 0.0

    @pytest.mark.asyncio
    async def test_routing_changes_reflected_in_report(self) -> None:
        """When policy routes to a cheaper model, savings should be positive."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                trace_id="trace-1",
                model="claude-opus-4-20250514",
                estimated_cost=1.0,
                task_type="classification",
                latency_ms=500.0,
            ),
            _mock_row(
                trace_id="trace-2",
                model="claude-opus-4-20250514",
                estimated_cost=2.0,
                task_type="classification",
                latency_ms=600.0,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        fitness_entries = [
            FitnessEntry(
                model="claude-haiku-4-5-20251001",
                task_type="classification",
                avg_quality=0.92,
                avg_cost=0.0008,
                avg_latency=200.0,
                sample_size=100,
            ),
        ]
        cache = _make_cache(fitness_entries)

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

        report = await dry_run(
            policy=policy,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session=session,
            fitness_cache=cache,
        )

        assert report.total_events == 2
        assert report.events_affected == 2
        assert report.cost_savings > 0
        assert report.savings_pct > 0
        assert len(report.model_distribution) > 0

    @pytest.mark.asyncio
    async def test_passthrough_policy_shows_no_changes(self) -> None:
        """Empty policy means no routing changes -- zero savings."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                trace_id="trace-1",
                model="claude-sonnet-4-20250514",
                estimated_cost=0.5,
                task_type="code_generation",
                latency_ms=800.0,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = await dry_run(
            policy=policy,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session=session,
            fitness_cache=cache,
        )

        assert report.total_events == 1
        assert report.events_affected == 0
        assert report.cost_savings == 0.0

    @pytest.mark.asyncio
    async def test_sample_decisions_limited(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                trace_id=f"trace-{i}",
                model="claude-sonnet-4-20250514",
                estimated_cost=0.1,
                task_type="classification",
                latency_ms=300.0,
            )
            for i in range(50)
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        cache = _make_cache([])
        policy = RoutingPolicy(rules=[])

        report = await dry_run(
            policy=policy,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session=session,
            fitness_cache=cache,
            sample_limit=5,
        )

        assert report.total_events == 50
        assert len(report.sample_decisions) == 5

    @pytest.mark.asyncio
    async def test_model_distribution_tracks_shifts(self) -> None:
        """Verify that model distribution correctly counts original vs routed."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                trace_id="trace-1",
                model="claude-opus-4-20250514",
                estimated_cost=1.0,
                task_type="classification",
                latency_ms=500.0,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        fitness_entries = [
            FitnessEntry(
                model="gpt-4o-mini",
                task_type="classification",
                avg_quality=0.91,
                avg_cost=0.0004,
                avg_latency=200.0,
                sample_size=100,
            ),
        ]
        cache = _make_cache(fitness_entries)

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

        report = await dry_run(
            policy=policy,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session=session,
            fitness_cache=cache,
        )

        # opus should have original_count=1, routed_count=0
        # gpt-4o-mini should have original_count=0, routed_count=1
        opus_dist = next(
            (d for d in report.model_distribution if d.model == "claude-opus-4-20250514"),
            None,
        )
        mini_dist = next(
            (d for d in report.model_distribution if d.model == "gpt-4o-mini"),
            None,
        )

        assert opus_dist is not None
        assert opus_dist.original_count == 1
        assert opus_dist.routed_count == 0

        assert mini_dist is not None
        assert mini_dist.original_count == 0
        assert mini_dist.routed_count == 1
