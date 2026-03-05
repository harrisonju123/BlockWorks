"""Listing verification logic.

A listing is "verified" when it meets minimum thresholds for trust,
uptime, usage, and economic stake. Verification is a snapshot check --
listings can lose verified status if metrics degrade.
"""

from __future__ import annotations

from blockthrough.registry.store import ListingNotFoundError, RegistryStore
from blockthrough.trust.registry import AgentNotRegisteredError, TrustRegistry


class VerificationResult:
    """Detailed pass/fail breakdown for each verification criterion."""

    def __init__(
        self,
        trust_ok: bool,
        trust_value: float,
        uptime_ok: bool,
        uptime_value: float,
        calls_ok: bool,
        calls_value: int,
        stake_ok: bool,
        stake_value: float,
    ) -> None:
        self.trust_ok = trust_ok
        self.trust_value = trust_value
        self.uptime_ok = uptime_ok
        self.uptime_value = uptime_value
        self.calls_ok = calls_ok
        self.calls_value = calls_value
        self.stake_ok = stake_ok
        self.stake_value = stake_value

    @property
    def is_verified(self) -> bool:
        return self.trust_ok and self.uptime_ok and self.calls_ok and self.stake_ok

    def to_dict(self) -> dict:
        return {
            "is_verified": self.is_verified,
            "trust": {"passed": self.trust_ok, "value": self.trust_value},
            "uptime": {"passed": self.uptime_ok, "value": self.uptime_value},
            "calls": {"passed": self.calls_ok, "value": self.calls_value},
            "stake": {"passed": self.stake_ok, "value": self.stake_value},
        }


def verify_listing(
    listing_id: str,
    store: RegistryStore,
    trust_registry: TrustRegistry | None = None,
    *,
    min_trust: float = 0.6,
    min_uptime: float = 95.0,
    min_calls: int = 100,
    min_stake: float = 0.01,
) -> bool:
    """Check if a listing meets all verification criteria.

    Returns True if all thresholds are met, False otherwise.

    Raises:
        ListingNotFoundError: If listing_id does not exist.
    """
    result = get_verification_status(
        listing_id,
        store,
        trust_registry,
        min_trust=min_trust,
        min_uptime=min_uptime,
        min_calls=min_calls,
        min_stake=min_stake,
    )
    return result.is_verified


def get_verification_status(
    listing_id: str,
    store: RegistryStore,
    trust_registry: TrustRegistry | None = None,
    *,
    min_trust: float = 0.6,
    min_uptime: float = 95.0,
    min_calls: int = 100,
    min_stake: float = 0.01,
) -> VerificationResult:
    """Get a detailed verification breakdown for a listing.

    Raises:
        ListingNotFoundError: If listing_id does not exist.
    """
    listing = store.get_listing(listing_id)

    # Pull live trust score if available
    trust_score = listing.trust_score
    if trust_registry is not None:
        try:
            trust_score = trust_registry.get_score(
                listing.owner_address
            ).composite_score
        except AgentNotRegisteredError:
            pass

    return VerificationResult(
        trust_ok=trust_score >= min_trust,
        trust_value=trust_score,
        uptime_ok=listing.uptime_pct >= min_uptime,
        uptime_value=listing.uptime_pct,
        calls_ok=listing.total_calls >= min_calls,
        calls_value=listing.total_calls,
        stake_ok=listing.stake_amount >= min_stake,
        stake_value=listing.stake_amount,
    )
