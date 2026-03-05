"""Tests for challenge settlement in ValidatorEconomics.

Validates that settle_challenge correctly slashes yes-voters when the
challenger wins, returns bond + 50% of slash proceeds, and forfeits
bond when the challenger loses.
"""

from __future__ import annotations

import pytest

from blockthrough.validators.consensus import ConsensusEngine
from blockthrough.validators.economics import ValidatorEconomics
from blockthrough.validators.registry import ValidatorRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_system(
    validators: dict[str, float],
    slash_percentage: float = 0.05,
) -> tuple[ValidatorRegistry, ValidatorEconomics]:
    registry = ValidatorRegistry(min_stake=0.1)
    for addr, stake in validators.items():
        registry.register(addr, stake)

    consensus = ConsensusEngine(threshold=2, tolerance=0.1)
    economics = ValidatorEconomics(
        registry=registry,
        consensus=consensus,
        slash_percentage=slash_percentage,
    )
    return registry, economics


# ---------------------------------------------------------------------------
# Challenger wins
# ---------------------------------------------------------------------------


class TestChallengerWins:

    def test_yes_voters_slashed(self) -> None:
        """All yes-voters lose slash_percentage of their stake."""
        registry, economics = _build_system(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            slash_percentage=0.05,
        )

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA", "0xB", "0xC"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # Alice: 3.0 * 0.05 = 0.15
        # Bob:   2.0 * 0.05 = 0.10
        # Carol: 1.0 * 0.05 = 0.05
        assert settlements["0xA"] == pytest.approx(-0.15)
        assert settlements["0xB"] == pytest.approx(-0.10)
        assert settlements["0xC"] == pytest.approx(-0.05)

        # Verify registry mutations
        assert registry.get_validator("0xA").stake_amount == pytest.approx(2.85)
        assert registry.get_validator("0xB").stake_amount == pytest.approx(1.90)
        assert registry.get_validator("0xC").stake_amount == pytest.approx(0.95)

    def test_challenger_reward(self) -> None:
        """Challenger gets bond + 50% of total slashed."""
        _, economics = _build_system(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            slash_percentage=0.05,
        )

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA", "0xB", "0xC"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # Total slashed: 0.15 + 0.10 + 0.05 = 0.30
        # Reward: 0.01 (bond) + 0.15 (50% of 0.30) = 0.16
        assert settlements["0xE"] == pytest.approx(0.16)

    def test_partial_yes_voters_only_slashed(self) -> None:
        """Only yes-voters are slashed, not all voters."""
        registry, economics = _build_system(
            {"0xA": 3.0, "0xB": 2.0, "0xC": 1.0},
            slash_percentage=0.10,
        )

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA"],  # Only Alice voted yes
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # Only Alice slashed: 3.0 * 0.10 = 0.30
        assert settlements["0xA"] == pytest.approx(-0.30)
        assert "0xB" not in settlements
        assert "0xC" not in settlements

        # Bob and Carol untouched
        assert registry.get_validator("0xB").stake_amount == pytest.approx(2.0)
        assert registry.get_validator("0xC").stake_amount == pytest.approx(1.0)

    def test_deactivation_on_heavy_slash(self) -> None:
        """Validator deactivated if slashed below min_stake."""
        registry, economics = _build_system(
            {"0xA": 0.2},
            slash_percentage=0.80,
        )

        economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # 0.2 * 0.80 = 0.16 slashed → 0.04 remaining < min_stake(0.1)
        info = registry.get_validator("0xA")
        assert info.stake_amount == pytest.approx(0.04)
        assert info.is_active is False


# ---------------------------------------------------------------------------
# Challenger loses
# ---------------------------------------------------------------------------


class TestChallengerLoses:

    def test_bond_forfeited(self) -> None:
        """Challenger's bond is forfeited (negative settlement)."""
        _, economics = _build_system({"0xA": 3.0, "0xB": 2.0, "0xC": 1.0})

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA", "0xB", "0xC"],
            challenger_address="0xE",
            bond=0.05,
            challenger_wins=False,
        )

        assert settlements["0xE"] == pytest.approx(-0.05)
        # No one else is affected
        assert len(settlements) == 1

    def test_stakes_unchanged_on_loss(self) -> None:
        """Yes-voter stakes are not modified when challenger loses."""
        registry, economics = _build_system({"0xA": 3.0, "0xB": 2.0})

        economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA", "0xB"],
            challenger_address="0xE",
            bond=0.05,
            challenger_wins=False,
        )

        assert registry.get_validator("0xA").stake_amount == pytest.approx(3.0)
        assert registry.get_validator("0xB").stake_amount == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_yes_voters(self) -> None:
        """Challenge with no yes-voters: challenger gets only bond back."""
        _, economics = _build_system({"0xA": 1.0})

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=[],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # No one slashed, so reward = bond + 50%(0) = bond
        assert settlements["0xE"] == pytest.approx(0.01)

    def test_unknown_validator_in_yes_voters_skipped(self) -> None:
        """Validators not in registry are silently skipped."""
        _, economics = _build_system({"0xA": 1.0})

        settlements = economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA", "0xUNKNOWN"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )

        # Only 0xA slashed
        assert "0xA" in settlements
        assert "0xUNKNOWN" not in settlements

    def test_cumulative_slashes_from_challenges(self) -> None:
        """Successive challenges compound slashing on same validator."""
        registry, economics = _build_system(
            {"0xA": 1.0},
            slash_percentage=0.10,
        )

        # First challenge
        economics.settle_challenge(
            challenge_id="ch-1",
            yes_voters=["0xA"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )
        # 1.0 * 0.10 = 0.10 slashed → 0.90 remaining
        assert registry.get_validator("0xA").stake_amount == pytest.approx(0.90)

        # Second challenge
        economics.settle_challenge(
            challenge_id="ch-2",
            yes_voters=["0xA"],
            challenger_address="0xE",
            bond=0.01,
            challenger_wins=True,
        )
        # 0.90 * 0.10 = 0.09 slashed → 0.81 remaining
        assert registry.get_validator("0xA").stake_amount == pytest.approx(0.81)
        assert registry.get_validator("0xA").cumulative_slashes == pytest.approx(0.19)
