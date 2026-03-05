"""Tests for validator economics: rewards, slashing, and settlement.

Validates that rewards are split equally among agreeing validators,
outliers are slashed proportionally, and cumulative tracking works.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.validators.consensus import ConsensusEngine
from blockthrough.validators.economics import EconomicsError, ValidatorEconomics
from blockthrough.validators.registry import ValidatorRegistry
from blockthrough.validators.types import ValidationSubmission


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_submission(
    task_id: str = "task-1",
    validator: str = "0xA",
    score: float = 0.8,
) -> ValidationSubmission:
    return ValidationSubmission(
        task_id=task_id,
        validator_address=validator,
        quality_score=score,
        judge_model="claude-haiku-4-5-20251001",
        submitted_at=datetime.now(timezone.utc),
        signature="sig",
    )


def _build_system(
    validators: dict[str, float],
    threshold: int = 2,
    tolerance: float = 0.1,
    slash_tolerance: float = 0.2,
    reward_per_task: float = 0.01,
    slash_percentage: float = 0.05,
) -> tuple[ValidatorRegistry, ConsensusEngine, ValidatorEconomics]:
    """Set up a fully wired registry + consensus + economics stack."""
    registry = ValidatorRegistry(min_stake=0.1)
    for addr, stake in validators.items():
        registry.register(addr, stake)

    consensus = ConsensusEngine(
        threshold=threshold,
        tolerance=tolerance,
        slash_tolerance=slash_tolerance,
    )
    economics = ValidatorEconomics(
        registry=registry,
        consensus=consensus,
        reward_per_task=reward_per_task,
        slash_percentage=slash_percentage,
    )
    return registry, consensus, economics


# ---------------------------------------------------------------------------
# Reward calculation
# ---------------------------------------------------------------------------


class TestRewardCalculation:

    def test_reward_split_equally_among_agreeing(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
            reward_per_task=0.03,
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.30))

        reward_a = economics.calculate_reward("task-1", "0xA")
        reward_b = economics.calculate_reward("task-1", "0xB")
        reward_c = economics.calculate_reward("task-1", "0xC")

        # A and B agree -> 0.03 / 2 = 0.015 each
        assert reward_a == pytest.approx(0.015)
        assert reward_b == pytest.approx(0.015)
        # C is an outlier -> no reward
        assert reward_c == 0.0

    def test_reward_zero_without_consensus(self) -> None:
        _, consensus, economics = _build_system(
            {"0xA": 1.0},
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))

        assert economics.calculate_reward("task-1", "0xA") == 0.0

    def test_reward_for_all_three_agreeing(self) -> None:
        _, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
            reward_per_task=0.03,
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.78))

        # All 3 agree -> 0.03 / 3 = 0.01 each
        for addr in ["0xA", "0xB", "0xC"]:
            assert economics.calculate_reward("task-1", addr) == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Slash calculation
# ---------------------------------------------------------------------------


class TestSlashCalculation:

    def test_slash_proportional_to_stake(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 2.0},
            slash_percentage=0.05,
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.30))

        slash_c = economics.calculate_slash("task-1", "0xC")
        assert slash_c == pytest.approx(2.0 * 0.05)

    def test_no_slash_for_agreeing_validator(self) -> None:
        _, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.30))

        assert economics.calculate_slash("task-1", "0xA") == 0.0
        assert economics.calculate_slash("task-1", "0xB") == 0.0

    def test_no_slash_without_consensus(self) -> None:
        _, consensus, economics = _build_system(
            {"0xA": 1.0},
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))

        assert economics.calculate_slash("task-1", "0xA") == 0.0


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


class TestSettlement:

    def test_settle_rewards_and_slashes(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
            reward_per_task=0.02,
            slash_percentage=0.05,
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.30))

        settlements = economics.settle_task("task-1")

        # A and B rewarded, C slashed
        assert settlements["0xA"] == pytest.approx(0.01)
        assert settlements["0xB"] == pytest.approx(0.01)
        assert settlements["0xC"] == pytest.approx(-0.05)

        # Verify registry state was mutated
        a_info = registry.get_validator("0xA")
        assert a_info is not None
        assert a_info.cumulative_rewards == pytest.approx(0.01)
        assert a_info.total_validations == 1

        c_info = registry.get_validator("0xC")
        assert c_info is not None
        assert c_info.cumulative_slashes == pytest.approx(0.05)
        assert c_info.stake_amount == pytest.approx(0.95)

    def test_settle_without_consensus_raises(self) -> None:
        _, consensus, economics = _build_system(
            {"0xA": 1.0},
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))

        with pytest.raises(EconomicsError, match="consensus not reached"):
            economics.settle_task("task-1")

    def test_settle_all_agree_no_slash(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
            reward_per_task=0.03,
        )
        consensus.submit_validation(_make_submission(validator="0xA", score=0.80))
        consensus.submit_validation(_make_submission(validator="0xB", score=0.82))
        consensus.submit_validation(_make_submission(validator="0xC", score=0.78))

        settlements = economics.settle_task("task-1")

        # All three rewarded, none slashed
        for addr in ["0xA", "0xB", "0xC"]:
            assert settlements[addr] == pytest.approx(0.01)

        # No slashes in registry
        for addr in ["0xA", "0xB", "0xC"]:
            info = registry.get_validator(addr)
            assert info is not None
            assert info.cumulative_slashes == 0.0


# ---------------------------------------------------------------------------
# Cumulative tracking across multiple tasks
# ---------------------------------------------------------------------------


class TestCumulativeTracking:

    def test_rewards_accumulate_across_tasks(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0},
            reward_per_task=0.02,
        )

        # Task 1
        consensus.submit_validation(
            _make_submission(task_id="task-1", validator="0xA", score=0.80)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-1", validator="0xB", score=0.82)
        )
        economics.settle_task("task-1")

        # Task 2
        consensus.submit_validation(
            _make_submission(task_id="task-2", validator="0xA", score=0.70)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-2", validator="0xB", score=0.72)
        )
        economics.settle_task("task-2")

        info = registry.get_validator("0xA")
        assert info is not None
        # 0.01 per task x 2 tasks = 0.02
        assert info.cumulative_rewards == pytest.approx(0.02)
        assert info.total_validations == 2

    def test_slashes_accumulate(self) -> None:
        registry, consensus, economics = _build_system(
            {"0xA": 1.0, "0xB": 1.0, "0xC": 1.0},
            slash_percentage=0.05,
        )

        # Task 1: C is outlier
        consensus.submit_validation(
            _make_submission(task_id="task-1", validator="0xA", score=0.80)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-1", validator="0xB", score=0.82)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-1", validator="0xC", score=0.30)
        )
        economics.settle_task("task-1")

        # Task 2: C is outlier again
        consensus.submit_validation(
            _make_submission(task_id="task-2", validator="0xA", score=0.70)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-2", validator="0xB", score=0.72)
        )
        consensus.submit_validation(
            _make_submission(task_id="task-2", validator="0xC", score=0.10)
        )
        economics.settle_task("task-2")

        info = registry.get_validator("0xC")
        assert info is not None
        # First slash: 1.0 * 0.05 = 0.05
        # Second slash: 0.95 * 0.05 = 0.0475
        assert info.cumulative_slashes == pytest.approx(0.0975)
        assert info.stake_amount == pytest.approx(0.9025)
