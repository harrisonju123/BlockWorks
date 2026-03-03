"""Tests for attestation hashing functions.

Covers determinism, canonical serialization, ordering independence,
org ID pseudonymization, and the full build_merkle_root pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agentproof.attestation.hashing import (
    build_merkle_root,
    build_trace_leaf,
    hash_fitness_matrix,
    hash_metrics,
    hash_org_id,
)
from agentproof.attestation.merkle import EMPTY_LEAF, MerkleTree
from agentproof.attestation.types import AttestationMetrics, TraceEvaluation
from agentproof.benchmarking.types import FitnessEntry


class TestHashMetrics:
    """Metrics hashing determinism and canonical serialization."""

    def _make_metrics(self, **overrides) -> AttestationMetrics:
        defaults = {
            "total_spend": 1234.567891,
            "waste_score": 0.234567,
            "request_count": 10000,
            "failure_rate": 0.012345,
            "model_distribution": {"gpt-4o": 6000, "claude-haiku-4-5-20251001": 4000},
        }
        defaults.update(overrides)
        return AttestationMetrics(**defaults)

    def test_deterministic_same_input(self) -> None:
        metrics = self._make_metrics()
        assert hash_metrics(metrics) == hash_metrics(metrics)

    def test_output_is_64_char_hex(self) -> None:
        result = hash_metrics(self._make_metrics())
        assert len(result) == 64
        int(result, 16)

    def test_different_inputs_different_hash(self) -> None:
        a = hash_metrics(self._make_metrics(total_spend=100.0))
        b = hash_metrics(self._make_metrics(total_spend=200.0))
        assert a != b

    def test_float_rounding_prevents_drift(self) -> None:
        """Two metrics with floats that differ only past 6 decimals hash the same."""
        m1 = self._make_metrics(total_spend=1.0000001)
        m2 = self._make_metrics(total_spend=1.0000002)
        assert hash_metrics(m1) == hash_metrics(m2)

    def test_float_difference_within_6_decimals_changes_hash(self) -> None:
        m1 = self._make_metrics(total_spend=1.000001)
        m2 = self._make_metrics(total_spend=1.000002)
        assert hash_metrics(m1) != hash_metrics(m2)

    def test_model_distribution_order_independent(self) -> None:
        """Dict ordering shouldn't affect the hash because hash_content sorts keys."""
        m1 = self._make_metrics(model_distribution={"a": 1, "b": 2})
        m2 = self._make_metrics(model_distribution={"b": 2, "a": 1})
        assert hash_metrics(m1) == hash_metrics(m2)

    def test_empty_model_distribution(self) -> None:
        result = hash_metrics(self._make_metrics(model_distribution={}))
        assert len(result) == 64


class TestHashFitnessMatrix:
    """Fitness matrix hashing with internal sorting."""

    def _make_entries(self) -> list[FitnessEntry]:
        return [
            FitnessEntry(
                task_type="code_generation",
                model="gpt-4o",
                avg_quality=0.85,
                avg_cost=0.003,
                avg_latency=250.0,
                sample_size=100,
            ),
            FitnessEntry(
                task_type="classification",
                model="claude-haiku-4-5-20251001",
                avg_quality=0.92,
                avg_cost=0.001,
                avg_latency=100.0,
                sample_size=200,
            ),
        ]

    def test_deterministic(self) -> None:
        entries = self._make_entries()
        assert hash_fitness_matrix(entries) == hash_fitness_matrix(entries)

    def test_output_is_64_char_hex(self) -> None:
        result = hash_fitness_matrix(self._make_entries())
        assert len(result) == 64

    def test_ordering_does_not_matter(self) -> None:
        """Entries in any order produce the same hash (sorted internally)."""
        entries = self._make_entries()
        reversed_entries = list(reversed(entries))
        assert hash_fitness_matrix(entries) == hash_fitness_matrix(reversed_entries)

    def test_empty_list(self) -> None:
        result = hash_fitness_matrix([])
        assert len(result) == 64

    def test_different_entries_different_hash(self) -> None:
        entries = self._make_entries()
        modified = self._make_entries()
        modified[0] = FitnessEntry(
            task_type="code_generation",
            model="gpt-4o",
            avg_quality=0.99,
            avg_cost=0.003,
            avg_latency=250.0,
            sample_size=100,
        )
        assert hash_fitness_matrix(entries) != hash_fitness_matrix(modified)


class TestHashOrgId:
    """Org ID pseudonymization."""

    def test_deterministic(self) -> None:
        assert hash_org_id("acme-corp") == hash_org_id("acme-corp")

    def test_output_is_64_char_hex(self) -> None:
        result = hash_org_id("test-org")
        assert len(result) == 64
        int(result, 16)

    def test_different_orgs_different_hash(self) -> None:
        assert hash_org_id("org-a") != hash_org_id("org-b")

    def test_one_way(self) -> None:
        """Hash output doesn't contain the original org ID."""
        result = hash_org_id("acme-corp")
        assert "acme" not in result
        assert "corp" not in result


class TestBuildTraceLeaf:
    """Trace evaluation -> canonical leaf hash."""

    def _make_eval(self, **overrides) -> TraceEvaluation:
        defaults = {
            "trace_id": "trace-001",
            "model": "gpt-4o",
            "task_type": "code_generation",
            "cost": 0.045,
            "quality_score": 0.88,
            "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        }
        defaults.update(overrides)
        return TraceEvaluation(**defaults)

    def test_deterministic(self) -> None:
        e = self._make_eval()
        assert build_trace_leaf(e) == build_trace_leaf(e)

    def test_output_is_64_char_hex(self) -> None:
        result = build_trace_leaf(self._make_eval())
        assert len(result) == 64

    def test_different_trace_id_different_hash(self) -> None:
        a = build_trace_leaf(self._make_eval(trace_id="trace-001"))
        b = build_trace_leaf(self._make_eval(trace_id="trace-002"))
        assert a != b

    def test_float_rounding(self) -> None:
        """Cost values that differ only past 6 decimals hash the same."""
        a = build_trace_leaf(self._make_eval(cost=0.0450001))
        b = build_trace_leaf(self._make_eval(cost=0.0450002))
        assert a == b


class TestBuildMerkleRoot:
    """End-to-end: evaluations -> Merkle root."""

    def _make_evals(self, count: int) -> list[TraceEvaluation]:
        return [
            TraceEvaluation(
                trace_id=f"trace-{i:03d}",
                model="gpt-4o",
                task_type="code_generation",
                cost=0.01 * i,
                quality_score=0.9,
                timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            )
            for i in range(count)
        ]

    def test_empty_evaluations(self) -> None:
        result = build_merkle_root([])
        assert result == EMPTY_LEAF

    def test_single_evaluation(self) -> None:
        evals = self._make_evals(1)
        root = build_merkle_root(evals)
        assert len(root) == 64

    def test_deterministic(self) -> None:
        evals = self._make_evals(10)
        assert build_merkle_root(evals) == build_merkle_root(evals)

    def test_different_evaluations_different_root(self) -> None:
        a = build_merkle_root(self._make_evals(5))
        b = build_merkle_root(self._make_evals(6))
        assert a != b

    def test_consistent_with_manual_tree_construction(self) -> None:
        """build_merkle_root should produce the same result as manually
        building a MerkleTree from the same leaf data."""
        evals = self._make_evals(4)
        root_from_fn = build_merkle_root(evals)

        leaf_data = [build_trace_leaf(e) for e in evals]
        tree = MerkleTree(leaf_data)
        assert root_from_fn == tree.root
