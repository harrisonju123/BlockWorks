"""Tests for task distribution and stake-weighted validator selection.

Validates task creation, validator assignment with weighted randomness,
and edge cases around insufficient validators.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from blockthrough.validators.registry import ValidatorRegistry
from blockthrough.validators.tasks import TaskDistributionError, TaskDistributor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry_with_validators(
    stakes: dict[str, float],
    min_stake: float = 0.1,
) -> ValidatorRegistry:
    """Helper: build a registry pre-populated with validators."""
    registry = ValidatorRegistry(min_stake=min_stake)
    for addr, amount in stakes.items():
        registry.register(addr, amount)
    return registry


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------


class TestTaskCreation:

    def test_create_task_returns_valid_task(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        distributor = TaskDistributor(registry=registry)

        task = distributor.create_task(
            benchmark_model="gpt-4o-mini",
            task_type="code_generation",
            prompt_hash="aa" * 32,
            completion_hash="bb" * 32,
        )

        assert task.task_id
        assert task.benchmark_model == "gpt-4o-mini"
        assert task.task_type == "code_generation"
        assert task.prompt_hash == "aa" * 32
        assert task.original_completion_hash == "bb" * 32
        assert task.deadline > task.created_at

    def test_create_task_stores_for_lookup(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        distributor = TaskDistributor(registry=registry)

        task = distributor.create_task(
            benchmark_model="gpt-4o-mini",
            task_type="code_generation",
            prompt_hash="aa" * 32,
            completion_hash="bb" * 32,
        )

        retrieved = distributor.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    def test_get_nonexistent_task_returns_none(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        distributor = TaskDistributor(registry=registry)
        assert distributor.get_task("nonexistent-id") is None

    def test_tasks_get_unique_ids(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        distributor = TaskDistributor(registry=registry)

        t1 = distributor.create_task("m1", "t1", "h1", "c1")
        t2 = distributor.create_task("m1", "t1", "h1", "c1")
        assert t1.task_id != t2.task_id


# ---------------------------------------------------------------------------
# Validator assignment
# ---------------------------------------------------------------------------


class TestValidatorAssignment:

    def test_assign_validators_returns_requested_count(self) -> None:
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0}
        )
        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        assigned = distributor.assign_validators(task.task_id, count=3)
        assert len(assigned) == 3

    def test_assign_updates_task(self) -> None:
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0}
        )
        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        assigned = distributor.assign_validators(task.task_id, count=2)
        updated = distributor.get_task(task.task_id)
        assert updated is not None
        assert len(updated.assigned_validators) == 2
        assert set(assigned) == set(updated.assigned_validators)

    def test_assign_nonexistent_task_raises(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        distributor = TaskDistributor(registry=registry)

        with pytest.raises(TaskDistributionError, match="not found"):
            distributor.assign_validators("nonexistent-id")

    def test_assign_insufficient_validators_raises(self) -> None:
        registry = _make_registry_with_validators({"0xA": 1.0, "0xB": 1.0})
        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        with pytest.raises(TaskDistributionError, match="only 2 eligible"):
            distributor.assign_validators(task.task_id, count=3)

    def test_assign_no_duplicate_validators(self) -> None:
        """A single assignment call never returns the same validator twice."""
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0, "0xD": 1.0, "0xE": 1.0}
        )
        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        assigned = distributor.assign_validators(task.task_id, count=5)
        assert len(set(assigned)) == 5

    def test_second_assignment_excludes_already_assigned(self) -> None:
        """Validators already assigned to a task are excluded from future assignment."""
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0, "0xD": 1.0}
        )
        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        first = distributor.assign_validators(task.task_id, count=2)
        second = distributor.assign_validators(task.task_id, count=2)

        all_assigned = set(first + second)
        assert len(all_assigned) == 4


# ---------------------------------------------------------------------------
# Weighted selection
# ---------------------------------------------------------------------------


class TestWeightedSelection:

    def test_higher_stake_selected_more_often(self) -> None:
        """Statistical test: validator with 10x stake should be selected
        significantly more often over many trials."""
        registry = _make_registry_with_validators(
            {"0xWhale": 10.0, "0xShrimp": 1.0}
        )
        distributor = TaskDistributor(registry=registry)

        rng = random.Random(42)
        counts: Counter[str] = Counter()

        for _ in range(1000):
            task = distributor.create_task("m1", "t1", "h1", "c1")
            assigned = distributor.assign_validators(
                task.task_id, count=1, rng=rng
            )
            counts[assigned[0]] += 1

        # With 10:1 weight ratio, whale should be picked ~91% of the time
        assert counts["0xWhale"] > counts["0xShrimp"] * 5

    def test_deterministic_with_seed(self) -> None:
        """Same seed produces same assignment."""
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 2.0, "0xC": 3.0}
        )

        results = []
        for _ in range(2):
            distributor = TaskDistributor(registry=registry)
            task = distributor.create_task("m1", "t1", "h1", "c1")
            assigned = distributor.assign_validators(
                task.task_id, count=2, rng=random.Random(123)
            )
            results.append(tuple(assigned))

        assert results[0] == results[1]

    def test_equal_stakes_roughly_uniform(self) -> None:
        """When all validators have equal stake, selection should be
        approximately uniform."""
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0, "0xD": 1.0}
        )
        distributor = TaskDistributor(registry=registry)

        rng = random.Random(42)
        counts: Counter[str] = Counter()

        for _ in range(2000):
            task = distributor.create_task("m1", "t1", "h1", "c1")
            assigned = distributor.assign_validators(
                task.task_id, count=1, rng=rng
            )
            counts[assigned[0]] += 1

        # Each should be within 15%-35% of total (expected 25%)
        for addr in ["0xA", "0xB", "0xC", "0xD"]:
            ratio = counts[addr] / 2000
            assert 0.15 < ratio < 0.35, f"{addr} ratio {ratio} outside expected range"

    def test_inactive_validators_excluded(self) -> None:
        """Validators that have been deactivated (e.g. slashed below min)
        are not eligible for assignment."""
        registry = _make_registry_with_validators(
            {"0xA": 1.0, "0xB": 0.15, "0xC": 1.0}
        )
        # Slash B below minimum to deactivate
        registry.slash("0xB", 0.10, "deactivate")

        distributor = TaskDistributor(registry=registry)
        task = distributor.create_task("m1", "t1", "h1", "c1")

        assigned = distributor.assign_validators(task.task_id, count=2)
        assert "0xB" not in assigned
        assert len(assigned) == 2
