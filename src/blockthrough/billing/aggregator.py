"""Usage aggregator — queries llm_events for independently observed token counts.

Groups by (provider, model) over a billing period to produce the "our count"
side of the reconciliation. Uses raw llm_events rather than continuous
aggregates because billing verification needs exact per-event token sums,
not pre-rolled approximations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from blockthrough.billing.types import ProviderUsage


async def aggregate_usage(
    session: AsyncSession,
    org_id: str | None,
    start: datetime,
    end: datetime,
) -> list[ProviderUsage]:
    """Aggregate observed token usage from llm_events by (provider, model).

    Only counts successful events — failed requests shouldn't appear on
    a provider invoice (providers don't bill for 4xx/5xx responses).
    """
    org_filter = "AND org_id = :org_id" if org_id else ""

    query = text(f"""
        SELECT
            provider,
            model,
            SUM(prompt_tokens) AS total_prompt_tokens,
            SUM(completion_tokens) AS total_completion_tokens,
            SUM(estimated_cost) AS total_cost,
            COUNT(*) AS request_count
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
          AND status = 'success'
          {org_filter}
        GROUP BY provider, model
        ORDER BY total_cost DESC
    """)

    params: dict = {"start": start, "end": end}
    if org_id:
        params["org_id"] = org_id

    result = await session.execute(query, params)

    return [
        ProviderUsage(
            provider=row["provider"],
            model=row["model"],
            period_start=start,
            period_end=end,
            observed_prompt_tokens=int(row["total_prompt_tokens"] or 0),
            observed_completion_tokens=int(row["total_completion_tokens"] or 0),
            observed_cost=float(row["total_cost"] or 0),
            observed_request_count=int(row["request_count"] or 0),
        )
        for row in [dict(r._mapping) for r in result.fetchall()]
    ]
