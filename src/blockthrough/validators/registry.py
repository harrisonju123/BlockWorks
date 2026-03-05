"""In-memory validator registry.

Follows the LocalProvider pattern from the attestation subsystem:
all state lives in memory for local development. Production would
back this with on-chain reads from the staking contract.
"""

from __future__ import annotations

import logging
from blockthrough.utils import utcnow
from blockthrough.validators.types import ValidatorInfo

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    """Raised when a registry operation violates an invariant."""


class ValidatorRegistry:
    """Manages validator registration, staking, and slashing.

    Enforces minimum stake and tracks validator lifecycle. The in-memory
    store mirrors what the staking contract would enforce on-chain.
    """

    def __init__(self, min_stake: float = 0.1) -> None:
        self._validators: dict[str, ValidatorInfo] = {}
        self._min_stake = min_stake

    @property
    def min_stake(self) -> float:
        return self._min_stake

    def register(self, address: str, stake_amount: float) -> ValidatorInfo:
        """Register a new validator with the given stake.

        Raises RegistryError if the address is already registered or
        the stake is below the minimum threshold.
        """
        if not address:
            raise RegistryError("Validator address must not be empty")

        if address in self._validators:
            raise RegistryError(f"Validator {address} is already registered")

        if stake_amount < self._min_stake:
            raise RegistryError(
                f"Stake {stake_amount} is below minimum {self._min_stake}"
            )

        info = ValidatorInfo(
            address=address,
            stake_amount=stake_amount,
            registered_at=utcnow(),
            is_active=True,
        )
        self._validators[address] = info
        logger.info("Registered validator %s with stake %.4f", address, stake_amount)
        return info

    def deregister(self, address: str) -> float:
        """Deregister a validator and return their remaining stake.

        Raises RegistryError if the address is not registered.
        """
        info = self._get_or_raise(address)
        refund = info.stake_amount
        del self._validators[address]
        logger.info("Deregistered validator %s, refunding %.4f", address, refund)
        return refund

    def get_validator(self, address: str) -> ValidatorInfo | None:
        """Look up a validator by address. Returns None if not found."""
        return self._validators.get(address)

    def get_all_validators(self) -> list[ValidatorInfo]:
        """Return all registered validators regardless of status."""
        return list(self._validators.values())

    def get_active_validators(self) -> list[ValidatorInfo]:
        """Return all validators that are currently active and staked."""
        return [v for v in self._validators.values() if v.is_active]

    def slash(self, address: str, amount: float, reason: str) -> ValidatorInfo:
        """Reduce a validator's stake as penalty for misbehavior.

        If the remaining stake falls below the minimum, the validator
        is deactivated. The slash amount is capped at the current stake
        to avoid negative balances.
        """
        info = self._get_or_raise(address)

        actual_slash = min(amount, info.stake_amount)
        info.stake_amount -= actual_slash
        info.cumulative_slashes += actual_slash

        if info.stake_amount < self._min_stake:
            info.is_active = False

        logger.warning(
            "Slashed validator %s by %.4f (reason: %s), remaining stake: %.4f",
            address,
            actual_slash,
            reason,
            info.stake_amount,
        )
        return info

    def reward(self, address: str, amount: float) -> ValidatorInfo:
        """Credit a validator with a reward for honest participation."""
        info = self._get_or_raise(address)
        info.cumulative_rewards += amount
        return info

    def update_accuracy(self, address: str, new_accuracy: float) -> ValidatorInfo:
        """Update a validator's rolling accuracy score."""
        info = self._get_or_raise(address)
        info.accuracy_score = max(0.0, min(1.0, new_accuracy))
        return info

    def increment_validations(self, address: str) -> ValidatorInfo:
        """Bump the total validation count after a completed task."""
        info = self._get_or_raise(address)
        info.total_validations += 1
        return info

    def _get_or_raise(self, address: str) -> ValidatorInfo:
        info = self._validators.get(address)
        if info is None:
            raise RegistryError(f"Validator {address} is not registered")
        return info
