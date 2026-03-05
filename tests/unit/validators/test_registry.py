"""Tests for the ValidatorRegistry in-memory implementation.

Validates registration, deregistration, slashing, minimum stake
enforcement, and validator lifecycle management.
"""

from __future__ import annotations

import pytest

from blockthrough.validators.registry import RegistryError, ValidatorRegistry


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:

    def test_register_creates_active_validator(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        info = registry.register("0xAlice", 1.0)

        assert info.address == "0xAlice"
        assert info.stake_amount == 1.0
        assert info.is_active is True
        assert info.total_validations == 0
        assert info.accuracy_score == 1.0

    def test_register_at_minimum_stake(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        info = registry.register("0xAlice", 0.1)
        assert info.is_active is True

    def test_register_below_minimum_stake_rejected(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        with pytest.raises(RegistryError, match="below minimum"):
            registry.register("0xAlice", 0.05)

    def test_register_duplicate_address_rejected(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)
        with pytest.raises(RegistryError, match="already registered"):
            registry.register("0xAlice", 2.0)

    def test_register_empty_address_rejected(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        with pytest.raises(RegistryError, match="must not be empty"):
            registry.register("", 1.0)

    def test_register_sets_registered_at(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        info = registry.register("0xAlice", 1.0)
        assert info.registered_at is not None


# ---------------------------------------------------------------------------
# Deregistration
# ---------------------------------------------------------------------------


class TestDeregistration:

    def test_deregister_returns_stake(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.5)
        refund = registry.deregister("0xAlice")

        assert refund == 1.5
        assert registry.get_validator("0xAlice") is None

    def test_deregister_unknown_address_raises(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        with pytest.raises(RegistryError, match="not registered"):
            registry.deregister("0xNobody")

    def test_deregister_removes_from_active_list(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)
        registry.register("0xBob", 1.0)

        registry.deregister("0xAlice")
        active = registry.get_active_validators()

        addresses = [v.address for v in active]
        assert "0xAlice" not in addresses
        assert "0xBob" in addresses


# ---------------------------------------------------------------------------
# Slashing
# ---------------------------------------------------------------------------


class TestSlashing:

    def test_slash_reduces_stake(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        info = registry.slash("0xAlice", 0.2, "outlier score")
        assert info.stake_amount == pytest.approx(0.8)
        assert info.cumulative_slashes == pytest.approx(0.2)

    def test_slash_deactivates_below_minimum(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 0.15)

        info = registry.slash("0xAlice", 0.10, "dishonest")
        assert info.stake_amount == pytest.approx(0.05)
        assert info.is_active is False

    def test_slash_capped_at_current_stake(self) -> None:
        """Slash amount cannot drive stake negative."""
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 0.2)

        info = registry.slash("0xAlice", 10.0, "severe offense")
        assert info.stake_amount == 0.0
        assert info.cumulative_slashes == pytest.approx(0.2)
        assert info.is_active is False

    def test_slash_unknown_address_raises(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        with pytest.raises(RegistryError, match="not registered"):
            registry.slash("0xNobody", 0.1, "reason")

    def test_slash_tracks_cumulative(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        registry.slash("0xAlice", 0.1, "first")
        registry.slash("0xAlice", 0.2, "second")

        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.cumulative_slashes == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Active validators
# ---------------------------------------------------------------------------


class TestActiveValidators:

    def test_get_active_validators_returns_only_active(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)
        registry.register("0xBob", 0.15)

        # Slash Bob below minimum to deactivate
        registry.slash("0xBob", 0.10, "deactivate")

        active = registry.get_active_validators()
        assert len(active) == 1
        assert active[0].address == "0xAlice"

    def test_get_active_validators_empty_registry(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        assert registry.get_active_validators() == []


# ---------------------------------------------------------------------------
# Rewards and accuracy
# ---------------------------------------------------------------------------


class TestRewardsAndAccuracy:

    def test_reward_increments_cumulative(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        registry.reward("0xAlice", 0.01)
        registry.reward("0xAlice", 0.02)

        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.cumulative_rewards == pytest.approx(0.03)

    def test_update_accuracy(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        registry.update_accuracy("0xAlice", 0.85)
        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.accuracy_score == pytest.approx(0.85)

    def test_update_accuracy_clamped(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        registry.update_accuracy("0xAlice", 1.5)
        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.accuracy_score == 1.0

        registry.update_accuracy("0xAlice", -0.5)
        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.accuracy_score == 0.0

    def test_increment_validations(self) -> None:
        registry = ValidatorRegistry(min_stake=0.1)
        registry.register("0xAlice", 1.0)

        registry.increment_validations("0xAlice")
        registry.increment_validations("0xAlice")

        info = registry.get_validator("0xAlice")
        assert info is not None
        assert info.total_validations == 2


# ---------------------------------------------------------------------------
# Minimum stake config
# ---------------------------------------------------------------------------


class TestMinStakeConfig:

    def test_custom_min_stake(self) -> None:
        registry = ValidatorRegistry(min_stake=0.5)
        assert registry.min_stake == 0.5

        with pytest.raises(RegistryError, match="below minimum"):
            registry.register("0xAlice", 0.4)

        info = registry.register("0xAlice", 0.5)
        assert info.is_active is True
