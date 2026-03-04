"""Background scheduler that automatically generates and submits attestations.

Runs on a configurable interval, discovers orgs from captured LLM events,
and chains attestations using the existing builder + provider infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from agentproof.attestation.builder import ZERO_HASH, build_attestation
from agentproof.attestation.hashing import compute_chain_hash, hash_org_id
from agentproof.attestation.provider import AttestationError, AttestationProvider
from agentproof.config import get_config
from agentproof.db.queries import get_distinct_org_ids, get_earliest_event_time
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)

# Don't create attestations for periods shorter than this
_MIN_PERIOD = timedelta(minutes=5)


async def run_attestation_scheduler(
    provider: AttestationProvider,
    interval_s: int,
) -> None:
    """Periodically build and submit attestations for all known orgs.

    Follows the same cancel-on-shutdown pattern as _refresh_fitness_cache in app.py.
    """
    logger.info(
        "Attestation scheduler started (interval=%ds)", interval_s
    )

    while True:
        try:
            await asyncio.sleep(interval_s)
            await _run_cycle(provider)
        except asyncio.CancelledError:
            logger.info("Attestation scheduler shutting down")
            break
        except Exception:
            logger.exception("Attestation scheduler cycle failed, will retry")


async def _run_cycle(provider: AttestationProvider) -> None:
    """Single scheduler cycle: discover orgs, build & submit attestations."""
    from agentproof.api.deps import get_async_session

    cfg = get_config()
    now = utcnow()

    async with get_async_session() as session:
        db_org_ids = await get_distinct_org_ids(session)

    # Deduplicate while preserving insertion order (DB orgs first, config org appended)
    org_ids = list(dict.fromkeys(db_org_ids + [cfg.org_id or "default"]))

    submitted = 0
    for org_id in org_ids:
        try:
            tx_id = await _attest_org(provider, org_id, now)
            if tx_id:
                submitted += 1
                logger.info(
                    "Attestation submitted for org=%s tx=%s", org_id, tx_id
                )
        except AttestationError as e:
            logger.warning("Attestation failed for org=%s: %s", org_id, e)
        except Exception:
            logger.exception("Unexpected error attesting org=%s", org_id)

    if submitted:
        logger.info("Scheduler cycle complete: %d attestations submitted", submitted)


async def _attest_org(
    provider: AttestationProvider,
    org_id: str,
    now: datetime,
) -> str | None:
    """Build and submit one attestation for a single org. Returns tx_id or None if skipped."""
    from agentproof.api.deps import get_async_session

    org_hash = hash_org_id(org_id)
    latest = await provider.get_latest(org_hash)

    if latest:
        period_start = latest.period_end
        prev_hash = compute_chain_hash(latest)
        nonce = latest.nonce + 1
    else:
        # Cold path: need to discover earliest event time from DB
        period_start = None
        prev_hash = ZERO_HASH
        nonce = 1

    period_end = now

    async with get_async_session() as session:
        if period_start is None:
            earliest = await get_earliest_event_time(session, org_id)
            if earliest is None:
                return None
            period_start = earliest

        if (period_end - period_start) < _MIN_PERIOD:
            return None

        record = await build_attestation(
            session, org_id, period_start, period_end, prev_hash, nonce
        )

    tx_id = await provider.submit(record)
    return tx_id
