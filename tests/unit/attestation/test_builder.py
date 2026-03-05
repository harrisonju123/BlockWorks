"""Tests for the attestation builder orchestration.

Mocks DB queries to verify the builder correctly composes hashes
and produces a valid AttestationRecord.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from blockthrough.attestation.builder import ZERO_HASH, build_attestation
from blockthrough.attestation.hashing import (
    build_merkle_root,
    hash_fitness_matrix,
    hash_metrics,
    hash_org_id,
)
from blockthrough.attestation.types import AttestationMetrics, TraceEvaluation
from blockthrough.benchmarking.types import FitnessEntry


def _make_metrics() -> AttestationMetrics:
    return AttestationMetrics(
        total_spend=500.0,
        waste_score=0.15,
        request_count=2000,
        failure_rate=0.02,
        model_distribution={"gpt-4o": 1500, "claude-haiku-4-5-20251001": 500},
    )


def _make_evaluations() -> list[TraceEvaluation]:
    return [
        TraceEvaluation(
            trace_id=f"trace-{i:03d}",
            model="gpt-4o",
            task_type="code_generation",
            cost=0.05 * i,
            quality_score=0.85,
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        for i in range(5)
    ]


def _make_fitness_entries() -> list[FitnessEntry]:
    return [
        FitnessEntry(
            task_type="code_generation",
            model="gpt-4o",
            avg_quality=0.85,
            avg_cost=0.003,
            avg_latency=250.0,
            sample_size=100,
        ),
    ]


class TestBuildAttestation:
    """Integration test with mocked DB layer."""

    @pytest.mark.asyncio
    async def test_build_produces_valid_record(self) -> None:
        metrics = _make_metrics()
        evaluations = _make_evaluations()
        fitness = _make_fitness_entries()

        session = AsyncMock()

        with (
            patch(
                "blockthrough.attestation.builder.get_attestation_metrics",
                new_callable=AsyncMock,
                return_value=metrics,
            ) as mock_metrics,
            patch(
                "blockthrough.attestation.builder.get_trace_evaluations",
                new_callable=AsyncMock,
                return_value=evaluations,
            ) as mock_evals,
            patch(
                "blockthrough.db.queries.get_fitness_matrix",
                new_callable=AsyncMock,
                return_value=fitness,
            ),
        ):
            record = await build_attestation(
                session=session,
                org_id="test-org",
                period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 3, 2, tzinfo=timezone.utc),
            )

            assert record.org_id_hash == hash_org_id("test-org")
            assert record.metrics_hash == hash_metrics(metrics)
            assert record.benchmark_hash == hash_fitness_matrix(fitness)
            assert record.merkle_root == build_merkle_root(evaluations)
            assert record.prev_hash == ZERO_HASH
            assert record.nonce == 1

            # Verify queries were called with the right args
            mock_metrics.assert_awaited_once_with(
                session,
                "test-org",
                datetime(2026, 3, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 2, tzinfo=timezone.utc),
            )
            mock_evals.assert_awaited_once_with(
                session,
                "test-org",
                datetime(2026, 3, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 2, tzinfo=timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_build_with_prev_hash(self) -> None:
        """When a prev_hash is provided, it should be used instead of ZERO_HASH."""
        prev = "ab" * 32  # 64-char hex

        session = AsyncMock()

        with (
            patch(
                "blockthrough.attestation.builder.get_attestation_metrics",
                new_callable=AsyncMock,
                return_value=_make_metrics(),
            ),
            patch(
                "blockthrough.attestation.builder.get_trace_evaluations",
                new_callable=AsyncMock,
                return_value=_make_evaluations(),
            ),
            patch(
                "blockthrough.db.queries.get_fitness_matrix",
                new_callable=AsyncMock,
                return_value=_make_fitness_entries(),
            ),
        ):
            record = await build_attestation(
                session=session,
                org_id="test-org",
                period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 3, 2, tzinfo=timezone.utc),
                prev_hash=prev,
            )

            assert record.prev_hash == prev

    @pytest.mark.asyncio
    async def test_build_with_no_evaluations(self) -> None:
        """Builder should handle an empty evaluation list (empty Merkle tree)."""
        session = AsyncMock()

        with (
            patch(
                "blockthrough.attestation.builder.get_attestation_metrics",
                new_callable=AsyncMock,
                return_value=_make_metrics(),
            ),
            patch(
                "blockthrough.attestation.builder.get_trace_evaluations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "blockthrough.db.queries.get_fitness_matrix",
                new_callable=AsyncMock,
                return_value=_make_fitness_entries(),
            ),
        ):
            record = await build_attestation(
                session=session,
                org_id="empty-org",
                period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 3, 2, tzinfo=timezone.utc),
            )

            assert record.merkle_root == build_merkle_root([])
            assert len(record.org_id_hash) == 64
            assert len(record.metrics_hash) == 64

    @pytest.mark.asyncio
    async def test_all_hash_fields_are_64_char_hex(self) -> None:
        """Every hash field in the output should be a valid 64-char hex string."""
        session = AsyncMock()

        with (
            patch(
                "blockthrough.attestation.builder.get_attestation_metrics",
                new_callable=AsyncMock,
                return_value=_make_metrics(),
            ),
            patch(
                "blockthrough.attestation.builder.get_trace_evaluations",
                new_callable=AsyncMock,
                return_value=_make_evaluations(),
            ),
            patch(
                "blockthrough.db.queries.get_fitness_matrix",
                new_callable=AsyncMock,
                return_value=_make_fitness_entries(),
            ),
        ):
            record = await build_attestation(
                session=session,
                org_id="test-org",
                period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 3, 2, tzinfo=timezone.utc),
            )

            for field_name in [
                "org_id_hash",
                "metrics_hash",
                "benchmark_hash",
                "merkle_root",
                "prev_hash",
            ]:
                value = getattr(record, field_name)
                assert len(value) == 64, f"{field_name} is not 64 chars: {value}"
                int(value, 16)  # valid hex
