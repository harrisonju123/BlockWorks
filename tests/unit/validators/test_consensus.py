"""Tests for the consensus engine.

Validates agreement, disagreement, partial agreement, tolerance
boundaries, outlier detection, and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.validators.consensus import ConsensusEngine
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


# ---------------------------------------------------------------------------
# Submission acceptance
# ---------------------------------------------------------------------------


class TestSubmission:

    def test_submit_accepted(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        accepted = engine.submit_validation(_make_submission())
        assert accepted is True

    def test_duplicate_submission_rejected(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA"))
        rejected = engine.submit_validation(_make_submission(validator="0xA"))
        assert rejected is False

    def test_different_validators_accepted(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        assert engine.submit_validation(_make_submission(validator="0xA")) is True
        assert engine.submit_validation(_make_submission(validator="0xB")) is True

    def test_get_submissions_returns_all(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA"))
        engine.submit_validation(_make_submission(validator="0xB"))

        subs = engine.get_submissions("task-1")
        assert len(subs) == 2


# ---------------------------------------------------------------------------
# Consensus: agreement
# ---------------------------------------------------------------------------


class TestAgreement:

    def test_two_of_three_agree_exact(self) -> None:
        """2 validators submit identical scores, threshold=2 -> consensus."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.8))
        engine.submit_validation(_make_submission(validator="0xB", score=0.8))
        engine.submit_validation(_make_submission(validator="0xC", score=0.3))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True
        assert result.agreed_score == pytest.approx(0.8)

    def test_two_of_three_agree_within_tolerance(self) -> None:
        """2 validators within tolerance of median -> consensus."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.85))
        engine.submit_validation(_make_submission(validator="0xC", score=0.20))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True
        # Median of all is 0.80; A(0.80) and B(0.85) within 0.1 of 0.80
        # Agreed score = median of [0.80, 0.85] = 0.825
        assert result.agreed_score == pytest.approx(0.825)

    def test_three_of_three_agree(self) -> None:
        """All validators agree."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.70))
        engine.submit_validation(_make_submission(validator="0xB", score=0.75))
        engine.submit_validation(_make_submission(validator="0xC", score=0.72))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True
        assert result.agreed_score is not None

    def test_three_of_five_agree(self) -> None:
        """3 validators agree, threshold=3."""
        engine = ConsensusEngine(threshold=3, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.78))
        engine.submit_validation(_make_submission(validator="0xD", score=0.10))
        engine.submit_validation(_make_submission(validator="0xE", score=0.15))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True


# ---------------------------------------------------------------------------
# Consensus: disagreement
# ---------------------------------------------------------------------------


class TestDisagreement:

    def test_all_disagree(self) -> None:
        """No consensus when all scores are far apart."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.1))
        engine.submit_validation(_make_submission(validator="0xB", score=0.5))
        engine.submit_validation(_make_submission(validator="0xC", score=0.9))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is False
        assert result.agreed_score is None

    def test_insufficient_submissions(self) -> None:
        """No consensus when fewer submissions than threshold."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.8))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is False

    def test_no_submissions(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        result = engine.check_consensus("nonexistent-task")
        assert result.consensus_reached is False
        assert result.submissions == []


# ---------------------------------------------------------------------------
# Tolerance boundary
# ---------------------------------------------------------------------------


class TestToleranceBoundary:

    def test_exactly_at_tolerance_boundary(self) -> None:
        """Score exactly at tolerance boundary should be included."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.90))

        # Median is 0.85. A is |0.80-0.85|=0.05 within, B is |0.90-0.85|=0.05 within
        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True

    def test_just_outside_tolerance(self) -> None:
        """Scores just outside tolerance should not agree."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.50))
        engine.submit_validation(_make_submission(validator="0xB", score=0.72))

        # Median = 0.61. A: |0.50-0.61|=0.11 > 0.1. B: |0.72-0.61|=0.11 > 0.1
        result = engine.check_consensus("task-1")
        assert result.consensus_reached is False

    def test_tight_tolerance(self) -> None:
        """Very tight tolerance requires near-exact agreement."""
        engine = ConsensusEngine(threshold=2, tolerance=0.01)
        engine.submit_validation(_make_submission(validator="0xA", score=0.800))
        engine.submit_validation(_make_submission(validator="0xB", score=0.805))
        engine.submit_validation(_make_submission(validator="0xC", score=0.900))

        result = engine.check_consensus("task-1")
        assert result.consensus_reached is True


# ---------------------------------------------------------------------------
# Outlier detection
# ---------------------------------------------------------------------------


class TestOutliers:

    def test_outlier_detected(self) -> None:
        """Validator with score > slash_tolerance from agreed score is flagged."""
        engine = ConsensusEngine(threshold=2, tolerance=0.1, slash_tolerance=0.2)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        outliers = engine.get_outliers("task-1")
        assert "0xC" in outliers
        assert "0xA" not in outliers
        assert "0xB" not in outliers

    def test_no_outliers_when_all_agree(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1, slash_tolerance=0.2)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.78))

        outliers = engine.get_outliers("task-1")
        assert outliers == []

    def test_no_outliers_without_consensus(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1, slash_tolerance=0.2)
        engine.submit_validation(_make_submission(validator="0xA", score=0.1))
        engine.submit_validation(_make_submission(validator="0xB", score=0.5))
        engine.submit_validation(_make_submission(validator="0xC", score=0.9))

        outliers = engine.get_outliers("task-1")
        assert outliers == []


# ---------------------------------------------------------------------------
# Agreeing validators
# ---------------------------------------------------------------------------


class TestAgreeingValidators:

    def test_agreeing_validators_returned(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1, slash_tolerance=0.2)
        engine.submit_validation(_make_submission(validator="0xA", score=0.80))
        engine.submit_validation(_make_submission(validator="0xB", score=0.82))
        engine.submit_validation(_make_submission(validator="0xC", score=0.30))

        agreeing = engine.get_agreeing_validators("task-1")
        assert "0xA" in agreeing
        assert "0xB" in agreeing
        assert "0xC" not in agreeing

    def test_no_agreeing_without_consensus(self) -> None:
        engine = ConsensusEngine(threshold=2, tolerance=0.1)
        engine.submit_validation(_make_submission(validator="0xA", score=0.1))

        agreeing = engine.get_agreeing_validators("task-1")
        assert agreeing == []
