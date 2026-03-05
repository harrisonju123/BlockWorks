"""Tests for StakeWeightedConsensusEngine.

Validates stake-weighted supermajority, quorum enforcement, and the
interaction between score agreement and stake weighting.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.validators.consensus import StakeWeightedConsensusEngine
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


def _build_engine(
    validators: dict[str, float],
    min_quorum: int = 3,
    threshold: int = 2,
    tolerance: float = 0.1,
    slash_tolerance: float = 0.2,
) -> tuple[ValidatorRegistry, StakeWeightedConsensusEngine]:
    registry = ValidatorRegistry(min_stake=0.1)
    for addr, stake in validators.items():
        registry.register(addr, stake)

    engine = StakeWeightedConsensusEngine(
        registry=registry,
        threshold=threshold,
        tolerance=tolerance,
        slash_tolerance=slash_tolerance,
        min_quorum=min_quorum,
    )
    return registry, engine


# ---------------------------------------------------------------------------
# Supermajority
# ---------------------------------------------------------------------------


class TestSupermajority:

    def test_all_agree_supermajority(self) -> None:
        """All 3 validators agree → supermajority reached."""
        _, engine = _build_engine({"0xA": 3.0, "0xB": 2.0, "0xC": 1.0})
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.78))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True
        assert result.supermajority_reached is True
        assert result.yes_stake == pytest.approx(6.0)
        assert result.total_participating_stake == pytest.approx(6.0)

    def test_two_of_three_agree_with_enough_stake(self) -> None:
        """Alice(3) + Bob(2) agree, Carol(1) doesn't.
        yes_stake=5/6=83% > 66.67%, but only 2 agree and quorum=3 → fails."""
        _, engine = _build_engine({"0xA": 3.0, "0xB": 2.0, "0xC": 1.0})
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        result = engine.check_consensus("task-1")
        # Score agreement passes (threshold=2, A+B agree)
        # But quorum requires 3 agreeing validators
        assert result.consensus_reached is False
        assert result.supermajority_reached is True

    def test_high_stake_validator_cannot_solo_force(self) -> None:
        """Single whale can't reach consensus alone even with 90% of stake."""
        _, engine = _build_engine(
            {"0xA": 9.0, "0xB": 0.5, "0xC": 0.5},
            min_quorum=2,
            threshold=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.30))
        engine.submit_validation(_make_submission(validator="0xC", score=0.10))

        result = engine.check_consensus("task-1")
        # Only A agrees with the median (which is 0.30).
        # A's score 0.80 is far from median 0.30 → A doesn't agree either.
        assert result.consensus_reached is False


# ---------------------------------------------------------------------------
# Quorum
# ---------------------------------------------------------------------------


class TestQuorum:

    def test_quorum_enforcement(self) -> None:
        """2 validators agree on score with enough stake, but quorum=3."""
        _, engine = _build_engine(
            {"0xA": 5.0, "0xB": 5.0, "0xC": 0.5},
            min_quorum=3,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is False

    def test_quorum_met_with_lower_requirement(self) -> None:
        """Same scenario but quorum=2 → consensus reached."""
        _, engine = _build_engine(
            {"0xA": 5.0, "0xB": 5.0, "0xC": 0.5},
            min_quorum=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True
        assert result.supermajority_reached is True

    def test_quorum_of_one_rejected(self) -> None:
        """Even with min_quorum=1, need score agreement threshold too."""
        _, engine = _build_engine(
            {"0xA": 10.0},
            min_quorum=1,
            threshold=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))

        result = engine.check_consensus("task-1")
        # threshold=2 but only 1 submission → no score agreement
        assert result.consensus_reached is False


# ---------------------------------------------------------------------------
# Stake weighting
# ---------------------------------------------------------------------------


class TestStakeWeighting:

    def test_stake_fields_populated(self) -> None:
        """yes_stake and total_participating_stake are set correctly."""
        _, engine = _build_engine(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            min_quorum=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.78))

        result = engine.check_consensus("task-1")
        assert result.total_participating_stake == pytest.approx(6.0)
        assert result.yes_stake == pytest.approx(6.0)

    def test_no_stake_means_no_supermajority(self) -> None:
        """Deregistered validator's stake doesn't count."""
        registry, engine = _build_engine(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            min_quorum=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))

        # Deregister both before checking
        registry.deregister("0xA")
        registry.deregister("0xB")

        result = engine.check_consensus("task-1")
        # total_stake = 0 → no supermajority
        assert result.consensus_reached is False

    def test_mixed_stakes_correct_ratio(self) -> None:
        """Alice(3) agrees, Bob(2) disagrees, Carol(1) agrees.
        Agreeing stake = 4, total = 6, ratio = 66.67% — exactly at threshold."""
        _, engine = _build_engine(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            min_quorum=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.30))
        engine.submit_validation(_make_submission(validator="0xC", score=0.78))

        result = engine.check_consensus("task-1")
        # Median of [0.80, 0.30, 0.78] = 0.78
        # A(0.80) within 0.1 of 0.78 ✓, B(0.30) not ✗, C(0.78) ✓
        # Agreeing: A(3.0) + C(1.0) = 4.0, total = 6.0
        # 4/6 ≈ 0.6667 → exactly at 2/3 threshold → passes
        assert result.consensus_reached is True
        assert result.yes_stake == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Inherited behavior preserved
# ---------------------------------------------------------------------------


class TestInheritedBehavior:

    def test_outlier_detection_works(self) -> None:
        """Base class outlier detection still works through subclass."""
        _, engine = _build_engine(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            min_quorum=2,
        )
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        # get_outliers uses base class logic
        outliers = engine.get_outliers("task-1")
        assert "0xC" in outliers

    def test_submit_validation_dedup(self) -> None:
        """Duplicate submission rejection is inherited."""
        _, engine = _build_engine({"0xA": 1.0, "0xB": 1.0, "0xC": 1.0})
        assert engine.submit_validation(_make_submission(validator="0xA")) is True
        assert engine.submit_validation(_make_submission(validator="0xA")) is False

    def test_no_submissions_no_consensus(self) -> None:
        _, engine = _build_engine({"0xA": 1.0, "0xB": 1.0, "0xC": 1.0})
        result = engine.check_consensus("nonexistent")
        assert result.consensus_reached is False
        assert result.total_participating_stake == 0.0
