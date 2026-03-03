"""Consensus engine for decentralized benchmark validation.

Implements a threshold-based agreement protocol: when enough validators
submit quality scores within a configurable tolerance of each other,
consensus is reached and the agreed score is the median of the agreeing
submissions. Outlier validators are flagged for slashing.
"""

from __future__ import annotations

import logging
from statistics import median

from agentproof.validators.types import ConsensusResult, ValidationSubmission

logger = logging.getLogger(__name__)


class ConsensusError(Exception):
    """Raised when a consensus operation fails."""


class ConsensusEngine:
    """Collects validator submissions and determines consensus.

    The engine stores all submissions per task and checks whether the
    agreement threshold has been met. Consensus requires that at least
    `threshold` validators agree within `tolerance` of the median score.
    """

    def __init__(
        self,
        threshold: int = 2,
        tolerance: float = 0.1,
        slash_tolerance: float = 0.2,
    ) -> None:
        self._threshold = threshold
        self._tolerance = tolerance
        # Validators deviating beyond this from the agreed score get slashed
        self._slash_tolerance = slash_tolerance
        # task_id -> list of submissions
        self._submissions: dict[str, list[ValidationSubmission]] = {}

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def tolerance(self) -> float:
        return self._tolerance

    @property
    def slash_tolerance(self) -> float:
        return self._slash_tolerance

    def submit_validation(self, submission: ValidationSubmission) -> bool:
        """Record a validator's submission for a task.

        Returns True if the submission was accepted, False if the
        validator already submitted for this task.
        """
        task_subs = self._submissions.setdefault(submission.task_id, [])

        # Reject duplicate submissions from the same validator
        for existing in task_subs:
            if existing.validator_address == submission.validator_address:
                logger.warning(
                    "Duplicate submission from %s for task %s",
                    submission.validator_address,
                    submission.task_id,
                )
                return False

        task_subs.append(submission)
        return True

    def check_consensus(self, task_id: str) -> ConsensusResult:
        """Evaluate whether consensus has been reached for a task.

        Consensus algorithm:
        1. Compute the median of all submitted scores.
        2. Count how many submissions fall within ±tolerance of the median.
        3. If that count >= threshold, consensus is reached and the
           agreed score is the median of the agreeing submissions.
        """
        subs = self._submissions.get(task_id, [])

        result = ConsensusResult(
            task_id=task_id,
            submissions=list(subs),
            agreement_threshold=self._threshold,
        )

        if len(subs) < self._threshold:
            return result

        scores = [s.quality_score for s in subs]
        med = median(scores)

        # Find submissions that agree with the median within tolerance
        agreeing = [s for s in subs if abs(s.quality_score - med) <= self._tolerance]

        if len(agreeing) >= self._threshold:
            # Agreed score is the median of agreeing validators' scores
            agreed_score = median([s.quality_score for s in agreeing])
            result.agreed_score = round(agreed_score, 6)
            result.consensus_reached = True

        return result

    def get_outliers(self, task_id: str) -> list[str]:
        """Return addresses of validators whose scores deviate beyond
        the slash tolerance from the consensus score.

        Only meaningful after consensus is reached. Returns empty list
        if no consensus or no outliers.
        """
        consensus = self.check_consensus(task_id)
        if not consensus.consensus_reached or consensus.agreed_score is None:
            return []

        outliers = []
        for sub in consensus.submissions:
            if abs(sub.quality_score - consensus.agreed_score) > self._slash_tolerance:
                outliers.append(sub.validator_address)

        return outliers

    def get_agreeing_validators(self, task_id: str) -> list[str]:
        """Return addresses of validators whose scores fall within
        tolerance of the agreed score.

        Only meaningful after consensus is reached.
        """
        consensus = self.check_consensus(task_id)
        if not consensus.consensus_reached or consensus.agreed_score is None:
            return []

        return [
            sub.validator_address
            for sub in consensus.submissions
            if abs(sub.quality_score - consensus.agreed_score) <= self._tolerance
        ]

    def get_submissions(self, task_id: str) -> list[ValidationSubmission]:
        """Return all submissions for a task."""
        return list(self._submissions.get(task_id, []))
