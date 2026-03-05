"""Reward and slashing economics for decentralized validators.

Rewards are split equally among validators that agree on the consensus
score. Slashing penalizes validators whose scores deviate too far from
consensus, removing a configurable percentage of their stake.
"""

from __future__ import annotations

import logging

from blockthrough.validators.consensus import ConsensusEngine
from blockthrough.validators.registry import ValidatorRegistry

logger = logging.getLogger(__name__)


class EconomicsError(Exception):
    """Raised when a reward/slash calculation fails."""


class ValidatorEconomics:
    """Computes and applies rewards and slashing based on consensus outcomes.

    Delegates to the registry for stake mutations and to the consensus
    engine for agreement/outlier determination.
    """

    def __init__(
        self,
        registry: ValidatorRegistry,
        consensus: ConsensusEngine,
        reward_per_task: float = 0.01,
        slash_percentage: float = 0.05,
    ) -> None:
        self._registry = registry
        self._consensus = consensus
        self._reward_per_task = reward_per_task
        self._slash_percentage = slash_percentage

    @property
    def reward_per_task(self) -> float:
        return self._reward_per_task

    @property
    def slash_percentage(self) -> float:
        return self._slash_percentage

    def calculate_reward(self, task_id: str, validator_address: str) -> float:
        """Calculate the reward for a validator on a completed task.

        Rewards are split equally among all agreeing validators.
        Returns 0.0 if the validator didn't agree or consensus wasn't reached.
        """
        agreeing = self._consensus.get_agreeing_validators(task_id)
        if not agreeing or validator_address not in agreeing:
            return 0.0

        return self._reward_per_task / len(agreeing)

    def calculate_slash(self, task_id: str, validator_address: str) -> float:
        """Calculate the slash amount for an outlier validator.

        Slash is a percentage of the validator's current stake.
        Returns 0.0 if the validator isn't an outlier.
        """
        outliers = self._consensus.get_outliers(task_id)
        if validator_address not in outliers:
            return 0.0

        info = self._registry.get_validator(validator_address)
        if info is None:
            return 0.0

        return info.stake_amount * self._slash_percentage

    def settle_task(self, task_id: str) -> dict[str, float]:
        """Apply all rewards and slashes for a completed consensus task.

        Returns a dict of address -> net change (positive for rewards,
        negative for slashes). Only call after consensus is reached.
        """
        result = self._consensus.check_consensus(task_id)
        if not result.consensus_reached:
            raise EconomicsError(
                f"Cannot settle task {task_id}: consensus not reached"
            )

        # Derive agreeing/outlier sets from this single consensus snapshot
        # to avoid re-running check_consensus multiple times.
        agreed = result.agreed_score
        agreeing = [
            s.validator_address for s in result.submissions
            if abs(s.quality_score - agreed) <= self._consensus.tolerance
        ]
        outliers = [
            s.validator_address for s in result.submissions
            if abs(s.quality_score - agreed) > self._consensus.slash_tolerance
        ]

        settlements: dict[str, float] = {}

        # Reward agreeing validators
        reward_per = self._reward_per_task / len(agreeing) if agreeing else 0.0
        for address in agreeing:
            if reward_per > 0:
                self._registry.reward(address, reward_per)
                self._registry.increment_validations(address)
                settlements[address] = reward_per
                logger.info(
                    "Rewarded validator %s with %.6f for task %s",
                    address, reward_per, task_id,
                )

        # Slash outliers
        for address in outliers:
            info = self._registry.get_validator(address)
            slash_amount = info.stake_amount * self._slash_percentage if info else 0.0
            if slash_amount > 0:
                self._registry.slash(address, slash_amount, f"Outlier on task {task_id}")
                self._registry.increment_validations(address)
                settlements[address] = -slash_amount
                logger.warning(
                    "Slashed validator %s by %.6f for task %s",
                    address, slash_amount, task_id,
                )

        return settlements

    def settle_challenge(
        self,
        challenge_id: str,
        yes_voters: list[str],
        challenger_address: str,
        bond: float,
        challenger_wins: bool,
    ) -> dict[str, float]:
        """Settle a disputed attestation challenge.

        If challenger wins: slash all yes-voters (slash_percentage of their
        stake), return bond + 50% of total slashed amount to challenger.
        If challenger loses: bond is forfeited (no refund).

        Returns address -> net change mapping.
        """
        settlements: dict[str, float] = {}

        if challenger_wins:
            total_slashed = 0.0
            for address in yes_voters:
                info = self._registry.get_validator(address)
                if info is None:
                    continue
                slash_amount = info.stake_amount * self._slash_percentage
                if slash_amount > 0:
                    self._registry.slash(
                        address, slash_amount, f"Challenge {challenge_id} lost"
                    )
                    total_slashed += slash_amount
                    settlements[address] = -slash_amount
                    logger.warning(
                        "Challenge slash: validator %s lost %.6f for challenge %s",
                        address, slash_amount, challenge_id,
                    )

            # Challenger gets bond back + 50% of slash proceeds
            reward = bond + (total_slashed / 2)
            settlements[challenger_address] = reward
            logger.info(
                "Challenge %s: challenger %s wins, reward=%.6f (bond=%.6f + slash_share=%.6f)",
                challenge_id, challenger_address, reward, bond, total_slashed / 2,
            )
        else:
            # Challenger loses — bond forfeited
            settlements[challenger_address] = -bond
            logger.info(
                "Challenge %s: challenger %s loses, bond %.6f forfeited",
                challenge_id, challenger_address, bond,
            )

        return settlements
