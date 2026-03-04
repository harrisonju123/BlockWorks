"""Tests for the auto-attestation scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agentproof.utils import utcnow

import pytest

from agentproof.attestation.builder import ZERO_HASH
from agentproof.attestation.hashing import compute_chain_hash, hash_org_id
from agentproof.attestation.scheduler import _attest_org, _run_cycle

from .conftest import PERIOD_END, make_record

# Patch target for the lazy import inside scheduler functions
_DEPS = "agentproof.api.deps.get_async_session"


def _mock_session_cm(session):
    """Create an async context manager mock that yields session."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestAttestOrg:
    """Unit tests for _attest_org (single-org attestation logic)."""

    @pytest.mark.asyncio
    async def test_skips_when_no_events(self) -> None:
        """Should return None if there are no events for the org."""
        provider = AsyncMock()
        provider.get_latest.return_value = None

        session = AsyncMock()

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_earliest_event_time",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _attest_org(provider, "org-1", utcnow())

        assert result is None
        provider.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_period_too_short(self) -> None:
        """Should skip if time since earliest event < _MIN_PERIOD."""
        provider = AsyncMock()
        provider.get_latest.return_value = None

        now = datetime(2026, 3, 1, 0, 3, 0, tzinfo=timezone.utc)
        earliest = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

        session = AsyncMock()

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_earliest_event_time",
            new_callable=AsyncMock,
            return_value=earliest,
        ):
            result = await _attest_org(provider, "org-1", now)

        assert result is None

    @pytest.mark.asyncio
    async def test_first_attestation_uses_zero_hash(self) -> None:
        """First attestation should use ZERO_HASH as prev_hash and nonce=1."""
        provider = AsyncMock()
        provider.get_latest.return_value = None
        provider.submit.return_value = "local-tx-00000001"

        earliest = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 1, 2, 0, 0, tzinfo=timezone.utc)

        session = AsyncMock()

        built_record = make_record(
            org_id_hash=hash_org_id("org-1"),
            nonce=1,
            prev_hash=ZERO_HASH,
        )

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_earliest_event_time",
            new_callable=AsyncMock,
            return_value=earliest,
        ), patch(
            "agentproof.attestation.scheduler.build_attestation",
            new_callable=AsyncMock,
            return_value=built_record,
        ) as mock_build:
            result = await _attest_org(provider, "org-1", now)

        assert result == "local-tx-00000001"
        # build_attestation(session, org_id, period_start, period_end, prev_hash, nonce)
        args = mock_build.call_args[0]
        assert args[1] == "org-1"
        assert args[2] == earliest  # period_start
        assert args[4] == ZERO_HASH  # prev_hash
        assert args[5] == 1  # nonce
        provider.submit.assert_awaited_once_with(built_record)

    @pytest.mark.asyncio
    async def test_chained_attestation_uses_prev_hash(self) -> None:
        """Subsequent attestations should chain from the latest record."""
        prev_record = make_record(
            org_id_hash=hash_org_id("org-1"),
            nonce=1,
            prev_hash=ZERO_HASH,
            period_end=PERIOD_END,
        )
        expected_prev_hash = compute_chain_hash(prev_record)

        provider = AsyncMock()
        provider.get_latest.return_value = prev_record
        provider.submit.return_value = "local-tx-00000002"

        now = PERIOD_END + timedelta(hours=2)

        session = AsyncMock()

        chained_record = make_record(
            org_id_hash=hash_org_id("org-1"),
            nonce=2,
            prev_hash=expected_prev_hash,
            period_start=PERIOD_END,
        )

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.build_attestation",
            new_callable=AsyncMock,
            return_value=chained_record,
        ) as mock_build:
            result = await _attest_org(provider, "org-1", now)

        assert result == "local-tx-00000002"
        args = mock_build.call_args[0]
        assert args[2] == PERIOD_END  # period_start = previous period_end
        assert args[4] == expected_prev_hash
        assert args[5] == 2  # nonce


class TestRunCycle:
    """Tests for the full scheduler cycle."""

    @pytest.mark.asyncio
    async def test_cycle_processes_all_orgs(self) -> None:
        """Cycle should attempt attestation for each discovered org + config org."""
        provider = AsyncMock()

        session = AsyncMock()
        mock_config = MagicMock()
        mock_config.org_id = "config-org"

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_distinct_org_ids",
            new_callable=AsyncMock,
            return_value=["org-a", "org-b"],
        ), patch(
            "agentproof.attestation.scheduler.get_config",
            return_value=mock_config,
        ), patch(
            "agentproof.attestation.scheduler._attest_org",
            new_callable=AsyncMock,
            return_value="local-tx-00000001",
        ) as mock_attest:
            await _run_cycle(provider)

        # Should have been called for org-a, org-b, and config-org
        assert mock_attest.await_count == 3
        called_orgs = {call.args[1] for call in mock_attest.call_args_list}
        assert called_orgs == {"org-a", "org-b", "config-org"}

    @pytest.mark.asyncio
    async def test_cycle_deduplicates_config_org(self) -> None:
        """If config org is already in DB orgs, it shouldn't be processed twice."""
        provider = AsyncMock()

        session = AsyncMock()
        mock_config = MagicMock()
        mock_config.org_id = "org-a"  # same as a DB org

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_distinct_org_ids",
            new_callable=AsyncMock,
            return_value=["org-a", "org-b"],
        ), patch(
            "agentproof.attestation.scheduler.get_config",
            return_value=mock_config,
        ), patch(
            "agentproof.attestation.scheduler._attest_org",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_attest:
            await _run_cycle(provider)

        # org-a should only appear once
        assert mock_attest.await_count == 2
        called_orgs = {call.args[1] for call in mock_attest.call_args_list}
        assert called_orgs == {"org-a", "org-b"}

    @pytest.mark.asyncio
    async def test_cycle_continues_on_individual_failure(self) -> None:
        """If one org fails, the cycle should continue to the next."""
        provider = AsyncMock()

        session = AsyncMock()
        mock_config = MagicMock()
        mock_config.org_id = "default"

        call_count = 0

        async def _side_effect(prov, org_id, now):
            nonlocal call_count
            call_count += 1
            if org_id == "org-bad":
                raise RuntimeError("boom")
            return "local-tx-00000001"

        with patch(_DEPS, return_value=_mock_session_cm(session)), patch(
            "agentproof.attestation.scheduler.get_distinct_org_ids",
            new_callable=AsyncMock,
            return_value=["org-bad", "org-good"],
        ), patch(
            "agentproof.attestation.scheduler.get_config",
            return_value=mock_config,
        ), patch(
            "agentproof.attestation.scheduler._attest_org",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            await _run_cycle(provider)

        # All 3 orgs should have been attempted despite the error
        assert call_count == 3
