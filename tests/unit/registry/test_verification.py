"""Tests for listing verification — pass/fail per criterion and edge cases."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.registry.store import ListingNotFoundError, RegistryStore
from blockthrough.registry.types import AgentListing, ListingCategory
from blockthrough.registry.verification import get_verification_status, verify_listing
from blockthrough.trust.registry import TrustRegistry
from blockthrough.trust.types import TrustDimension


def _make_listing(**overrides) -> AgentListing:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="",
        name="Verifiable Agent",
        description="Meets all criteria",
        owner_address="0xowner",
        category=ListingCategory.AGENT,
        stake_amount=0.05,
        uptime_pct=99.0,
        total_calls=200,
        registered_at=now,
        last_active=now,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)


class TestVerifyListing:

    def test_all_criteria_met(self) -> None:
        """A listing with good trust, uptime, calls, and stake passes."""
        trust = TrustRegistry()
        trust.register_agent("0xowner")
        trust.update_score("0xowner", TrustDimension.RELIABILITY, 0.9)
        trust.update_score("0xowner", TrustDimension.QUALITY, 0.9)

        store = RegistryStore(trust_registry=trust)
        created = store.register_listing(_make_listing())

        assert verify_listing(
            created.id,
            store,
            trust,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.01,
        )

    def test_fails_trust(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(trust_score=0.3)
        )

        result = verify_listing(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.01,
        )
        assert result is False

    def test_fails_uptime(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(uptime_pct=90.0, trust_score=0.7)
        )

        assert not verify_listing(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.01,
        )

    def test_fails_calls(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(total_calls=50, trust_score=0.7)
        )

        assert not verify_listing(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.01,
        )

    def test_fails_stake(self) -> None:
        store = RegistryStore(min_stake=0.001)
        created = store.register_listing(
            _make_listing(stake_amount=0.005, trust_score=0.7)
        )

        assert not verify_listing(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.01,
        )

    def test_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            verify_listing("ghost", store, None)


class TestGetVerificationStatus:

    def test_returns_detailed_breakdown(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(
                trust_score=0.8,
                uptime_pct=99.0,
                total_calls=500,
                stake_amount=0.1,
            )
        )

        result = get_verification_status(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.05,
        )
        assert result.is_verified is True
        assert result.trust_ok is True
        assert result.uptime_ok is True
        assert result.calls_ok is True
        assert result.stake_ok is True

    def test_partial_failure_breakdown(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(
                trust_score=0.8,
                uptime_pct=90.0,  # fails
                total_calls=500,
                stake_amount=0.1,
            )
        )

        result = get_verification_status(
            created.id,
            store,
            None,
            min_trust=0.6,
            min_uptime=95.0,
            min_calls=100,
            min_stake=0.05,
        )
        assert result.is_verified is False
        assert result.trust_ok is True
        assert result.uptime_ok is False
        assert result.calls_ok is True
        assert result.stake_ok is True

    def test_to_dict_format(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(trust_score=0.8, stake_amount=0.1)
        )

        result = get_verification_status(created.id, store, None)
        d = result.to_dict()
        assert "is_verified" in d
        assert "trust" in d
        assert "passed" in d["trust"]
        assert "value" in d["trust"]

    def test_uses_live_trust_when_available(self) -> None:
        """When a TrustRegistry is provided, live scores override stored ones."""
        trust = TrustRegistry()
        trust.register_agent("0xowner")
        # Push trust score well above threshold
        trust.update_score("0xowner", TrustDimension.RELIABILITY, 1.0)
        trust.update_score("0xowner", TrustDimension.QUALITY, 1.0)
        trust.update_score("0xowner", TrustDimension.EFFICIENCY, 1.0)
        trust.update_score("0xowner", TrustDimension.USAGE, 1.0)

        store = RegistryStore(trust_registry=trust)
        # Register with low stored trust -- live trust should override
        created = store.register_listing(_make_listing())

        result = get_verification_status(
            created.id,
            store,
            trust,
            min_trust=0.6,
        )
        assert result.trust_ok is True
        assert result.trust_value > 0.6

    def test_not_found_raises(self) -> None:
        store = RegistryStore()
        with pytest.raises(ListingNotFoundError):
            get_verification_status("ghost", store, None)

    def test_boundary_trust_exactly_at_threshold(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(trust_score=0.6)
        )
        result = get_verification_status(
            created.id, store, None, min_trust=0.6
        )
        assert result.trust_ok is True

    def test_boundary_trust_just_below_threshold(self) -> None:
        store = RegistryStore()
        created = store.register_listing(
            _make_listing(trust_score=0.599)
        )
        result = get_verification_status(
            created.id, store, None, min_trust=0.6
        )
        assert result.trust_ok is False
