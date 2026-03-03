"""Tests for the WasteAnalyzer orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agentproof.benchmarking.types import FitnessEntry
from agentproof.waste.analyzer import WasteAnalyzer
from agentproof.waste.types import WasteCategory


def _now() -> datetime:
    return datetime(2026, 3, 1, tzinfo=timezone.utc)


def _an_hour_ago() -> datetime:
    return datetime(2026, 2, 28, 23, 0, tzinfo=timezone.utc)


class TestAnalyzerOrchestration:

    @pytest.mark.asyncio
    async def test_empty_data_returns_zero_waste(self) -> None:
        session = AsyncMock()

        with (
            patch("agentproof.waste.analyzer.get_waste_analysis", return_value=[]),
            patch("agentproof.waste.analyzer.get_fitness_matrix", return_value=[]),
            patch("agentproof.waste.analyzer.get_duplicate_tool_calls", return_value=[]),
            patch("agentproof.waste.analyzer.get_prompt_hash_duplicates", return_value=[]),
            patch("agentproof.waste.analyzer.get_trace_tool_patterns", return_value=[]),
        ):
            analyzer = WasteAnalyzer()
            report = await analyzer.analyze(session, _an_hour_ago(), _now())

        assert report.waste_score == 0.0
        assert report.total_savings == 0.0
        assert report.items == []

    @pytest.mark.asyncio
    async def test_merges_results_from_multiple_detectors(self) -> None:
        session = AsyncMock()

        usage_rows = [
            {
                "task_type": "classification",
                "model": "claude-opus-4-20250514",
                "call_count": 100,
                "total_cost": 50.0,
            }
        ]
        fitness = [
            FitnessEntry(
                task_type="classification",
                model="claude-haiku-4-5-20251001",
                avg_quality=0.95,
                avg_cost=0.001,
                avg_latency=200.0,
                sample_size=50,
            )
        ]
        dup_tool_rows = [
            {
                "trace_id": "t1",
                "tool_name": "read_file",
                "args_hash": "h1",
                "dup_count": 4,
                "estimated_cost_per_call": 0.05,
            }
        ]

        with (
            patch("agentproof.waste.analyzer.get_waste_analysis", return_value=usage_rows),
            patch("agentproof.waste.analyzer.get_fitness_matrix", return_value=fitness),
            patch("agentproof.waste.analyzer.get_duplicate_tool_calls", return_value=dup_tool_rows),
            patch("agentproof.waste.analyzer.get_prompt_hash_duplicates", return_value=[]),
            patch("agentproof.waste.analyzer.get_trace_tool_patterns", return_value=[]),
        ):
            analyzer = WasteAnalyzer()
            report = await analyzer.analyze(session, _an_hour_ago(), _now())

        assert report.total_savings > 0
        assert report.waste_score > 0
        categories = {item.category for item in report.items}
        assert WasteCategory.MODEL_OVERKILL in categories
        assert WasteCategory.REDUNDANT_CALLS in categories

    @pytest.mark.asyncio
    async def test_waste_score_is_savings_over_spend(self) -> None:
        session = AsyncMock()

        usage_rows = [
            {
                "task_type": "classification",
                "model": "claude-opus-4-20250514",
                "call_count": 100,
                "total_cost": 100.0,
            }
        ]
        fitness = [
            FitnessEntry(
                task_type="classification",
                model="claude-haiku-4-5-20251001",
                avg_quality=0.95,
                avg_cost=0.001,
                avg_latency=200.0,
                sample_size=50,
            )
        ]

        with (
            patch("agentproof.waste.analyzer.get_waste_analysis", return_value=usage_rows),
            patch("agentproof.waste.analyzer.get_fitness_matrix", return_value=fitness),
            patch("agentproof.waste.analyzer.get_duplicate_tool_calls", return_value=[]),
            patch("agentproof.waste.analyzer.get_prompt_hash_duplicates", return_value=[]),
            patch("agentproof.waste.analyzer.get_trace_tool_patterns", return_value=[]),
        ):
            analyzer = WasteAnalyzer()
            report = await analyzer.analyze(session, _an_hour_ago(), _now())

        expected_score = report.total_savings / report.total_spend
        assert report.waste_score == pytest.approx(expected_score, abs=1e-5)

    @pytest.mark.asyncio
    async def test_items_sorted_by_savings_descending(self) -> None:
        session = AsyncMock()

        usage_rows = [
            {"task_type": "classification", "model": "claude-opus-4-20250514", "call_count": 10, "total_cost": 10.0},
            {"task_type": "extraction", "model": "claude-opus-4-20250514", "call_count": 50, "total_cost": 80.0},
        ]
        fitness = [
            FitnessEntry(task_type="classification", model="claude-haiku-4-5-20251001", avg_quality=0.95, avg_cost=0.001, avg_latency=200.0, sample_size=50),
            FitnessEntry(task_type="extraction", model="claude-haiku-4-5-20251001", avg_quality=0.92, avg_cost=0.001, avg_latency=200.0, sample_size=50),
        ]

        with (
            patch("agentproof.waste.analyzer.get_waste_analysis", return_value=usage_rows),
            patch("agentproof.waste.analyzer.get_fitness_matrix", return_value=fitness),
            patch("agentproof.waste.analyzer.get_duplicate_tool_calls", return_value=[]),
            patch("agentproof.waste.analyzer.get_prompt_hash_duplicates", return_value=[]),
            patch("agentproof.waste.analyzer.get_trace_tool_patterns", return_value=[]),
        ):
            analyzer = WasteAnalyzer()
            report = await analyzer.analyze(session, _an_hour_ago(), _now())

        savings = [item.savings for item in report.items]
        assert savings == sorted(savings, reverse=True)

    @pytest.mark.asyncio
    async def test_generated_at_is_set(self) -> None:
        session = AsyncMock()

        with (
            patch("agentproof.waste.analyzer.get_waste_analysis", return_value=[]),
            patch("agentproof.waste.analyzer.get_fitness_matrix", return_value=[]),
            patch("agentproof.waste.analyzer.get_duplicate_tool_calls", return_value=[]),
            patch("agentproof.waste.analyzer.get_prompt_hash_duplicates", return_value=[]),
            patch("agentproof.waste.analyzer.get_trace_tool_patterns", return_value=[]),
        ):
            analyzer = WasteAnalyzer()
            report = await analyzer.analyze(session, _an_hour_ago(), _now())

        assert report.generated_at is not None
