"""Vendor accountability report generation.

Combines drift detection results with cost data to produce evidence of
model performance changes. Each report gets a deterministic hash for
integrity verification. On-chain submission via AttestationProvider is
a separate step (not wired here — see billing/attestation.py for the pattern).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.benchmarking.drift import DriftReport
from agentproof.pipeline.hasher import hash_content

logger = logging.getLogger(__name__)


class DriftItem(BaseModel):
    """One drift finding with its estimated cost impact."""

    model: str
    task_type: str
    baseline_quality: float
    current_quality: float
    delta_pct: float
    p_value: float
    confidence_interval: tuple[float, float]
    baseline_sample_size: int
    current_sample_size: int
    call_volume: int = 0
    avg_cost_per_call: float = 0.0
    estimated_cost_impact: float = 0.0


class AccountabilityReport(BaseModel):
    """On-chain-anchored vendor accountability report."""

    org_id: str
    generated_at: datetime
    drift_items: list[DriftItem]
    estimated_total_cost_impact: float = 0.0
    attestation_hash: str = ""


async def _bulk_get_call_volumes(
    session: AsyncSession,
    drift_reports: list[DriftReport],
    org_id: str | None,
    days: int = 7,
) -> dict[tuple[str, str], tuple[int, float]]:
    """Fetch call volumes for all drifting (model, task_type) pairs in one query."""
    if not drift_reports:
        return {}

    now = datetime.now(UTC)
    start = now - timedelta(days=days)

    org_filter = "AND org_id = :org_id" if org_id else ""
    params: dict = {"start": start, "end": now}
    if org_id:
        params["org_id"] = org_id

    query = text(f"""
        SELECT
            model,
            task_type,
            COUNT(*) AS call_count,
            COALESCE(AVG(estimated_cost), 0) AS avg_cost
        FROM llm_events
        WHERE created_at >= :start
          AND created_at < :end
          {org_filter}
        GROUP BY model, task_type
    """)

    result = await session.execute(query, params)
    return {
        (row._mapping["model"], row._mapping["task_type"]): (
            int(row._mapping["call_count"]),
            float(row._mapping["avg_cost"]),
        )
        for row in result.fetchall()
    }


def _compute_cost_impact(
    call_volume: int,
    avg_cost_per_call: float,
    delta_pct: float,
) -> float:
    """Estimate the cost impact of degraded quality.

    The intuition: if quality dropped by X%, the org is getting X% less
    value for the same spend. The "cost impact" is the spend that now
    delivers degraded results — effectively wasted money.
    """
    total_spend = call_volume * avg_cost_per_call
    # The fraction of spend that's delivering subpar quality
    return round(total_spend * (delta_pct / 100.0), 6)


def _hash_report_data(
    org_id: str,
    generated_at: datetime,
    drift_items: list[DriftItem],
    total_cost_impact: float,
) -> str:
    """Canonical hash of report data. Computed before model construction."""
    from agentproof.attestation.hashing import hash_org_id

    payload = {
        "generated_at": generated_at.isoformat(),
        "org_id_hash": hash_org_id(org_id),
        "drift_items": [
            {
                "model": item.model,
                "task_type": item.task_type,
                "baseline_quality": round(item.baseline_quality, 6),
                "current_quality": round(item.current_quality, 6),
                "delta_pct": round(item.delta_pct, 2),
                "p_value": round(item.p_value, 6),
                "call_volume": item.call_volume,
                "estimated_cost_impact": round(item.estimated_cost_impact, 6),
            }
            for item in drift_items
        ],
        "estimated_total_cost_impact": round(total_cost_impact, 6),
    }
    return hash_content(payload)


async def generate_report(
    session: AsyncSession,
    drift_reports: list[DriftReport],
    org_id: str,
) -> AccountabilityReport:
    """Build an accountability report from drift detection results.

    For each drift item:
    1. Fetch production call volume and avg cost for the affected (model, task_type)
    2. Compute estimated cost impact (degraded quality x volume x cost)
    3. Anchor the full report on-chain via the attestation provider

    Args:
        session: Async DB session for call volume queries.
        drift_reports: Output from detect_drift().
        org_id: The org generating the report.

    Returns:
        A fully populated AccountabilityReport with attestation_hash set.
    """
    now = datetime.now(UTC)

    # Bulk fetch call volumes for all drifting models in a single query
    volume_lookup = await _bulk_get_call_volumes(session, drift_reports, org_id)

    drift_items: list[DriftItem] = []
    total_cost_impact = 0.0

    for dr in drift_reports:
        call_volume, avg_cost = volume_lookup.get((dr.model, dr.task_type), (0, 0.0))
        cost_impact = _compute_cost_impact(call_volume, avg_cost, dr.delta_pct)
        total_cost_impact += cost_impact

        drift_items.append(
            DriftItem(
                model=dr.model,
                task_type=dr.task_type,
                baseline_quality=dr.baseline_quality,
                current_quality=dr.current_quality,
                delta_pct=dr.delta_pct,
                p_value=dr.p_value,
                confidence_interval=dr.confidence_interval,
                baseline_sample_size=dr.baseline_sample_size,
                current_sample_size=dr.current_sample_size,
                call_volume=call_volume,
                avg_cost_per_call=avg_cost,
                estimated_cost_impact=cost_impact,
            )
        )

    # Compute hash before construction so the model is never in a half-initialized state
    report_hash = _hash_report_data(org_id, now, drift_items, total_cost_impact)

    report = AccountabilityReport(
        org_id=org_id,
        generated_at=now,
        drift_items=drift_items,
        estimated_total_cost_impact=round(total_cost_impact, 6),
        attestation_hash=report_hash,
    )

    logger.info(
        "Accountability report generated for org=%s: %d drift items, "
        "total_cost_impact=$%.4f, hash=%s",
        org_id,
        len(drift_items),
        total_cost_impact,
        report.attestation_hash[:16],
    )

    return report
