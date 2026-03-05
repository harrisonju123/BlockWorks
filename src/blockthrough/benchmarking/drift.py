"""Drift detection — statistical comparison of rolling vs baseline benchmark scores.

Compares a 7-day rolling window of benchmark quality scores against a 30-day
baseline per (model, task_type). Flags degradation exceeding 5% at p<0.05
using Welch's t-test, which handles unequal variances and sample sizes.

The detection runs over the existing benchmark_results table — no schema changes
required. All timestamps are UTC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from scipy import stats
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Detection thresholds — intentionally conservative to reduce false positives.
# A 5% quality drop is meaningful; tighter thresholds would fire on normal variance.
DEGRADATION_THRESHOLD_PCT = 5.0
SIGNIFICANCE_LEVEL = 0.05
MIN_SAMPLE_SIZE = 5


@dataclass(frozen=True)
class DriftReport:
    """One detected performance drift for a (model, task_type) pair."""

    model: str
    task_type: str
    baseline_quality: float
    current_quality: float
    delta_pct: float
    p_value: float
    confidence_interval: tuple[float, float]
    baseline_sample_size: int
    current_sample_size: int
    first_detected_at: datetime


async def _fetch_window_scores(
    session: AsyncSession,
    model: str,
    task_type: str,
    start: datetime,
    end: datetime,
) -> list[float]:
    """Pull quality_score values for a (model, task_type) within a time window."""
    query = text("""
        SELECT quality_score
        FROM benchmark_results
        WHERE benchmark_model = :model
          AND task_type = :task_type
          AND created_at >= :start
          AND created_at < :end
        ORDER BY created_at
    """)
    result = await session.execute(
        query,
        {"model": model, "task_type": task_type, "start": start, "end": end},
    )
    return [float(row[0]) for row in result.fetchall()]


def compute_drift(
    baseline_scores: list[float],
    current_scores: list[float],
) -> tuple[float, float, tuple[float, float]] | None:
    """Run Welch's t-test to detect statistically significant quality degradation.

    Returns (delta_pct, p_value, confidence_interval) if degradation exceeds
    the threshold at the configured significance level. Returns None otherwise.

    Welch's t-test is appropriate here because the baseline (30-day) and current
    (7-day) windows will almost always have different sample sizes and may have
    different variance.
    """
    if len(baseline_scores) < MIN_SAMPLE_SIZE or len(current_scores) < MIN_SAMPLE_SIZE:
        return None

    baseline_mean = sum(baseline_scores) / len(baseline_scores)
    current_mean = sum(current_scores) / len(current_scores)

    if baseline_mean == 0:
        return None

    delta_pct = ((baseline_mean - current_mean) / baseline_mean) * 100

    # Only flag degradation (current worse than baseline), not improvements
    if delta_pct <= DEGRADATION_THRESHOLD_PCT:
        return None

    # Welch's t-test: unequal variances assumed
    t_stat, p_value = stats.ttest_ind(
        baseline_scores, current_scores, equal_var=False
    )

    if p_value >= SIGNIFICANCE_LEVEL:
        return None

    # 95% confidence interval for the difference in means
    from scipy.stats import t as t_dist
    import math

    n1, n2 = len(baseline_scores), len(current_scores)
    s1 = (sum((x - baseline_mean) ** 2 for x in baseline_scores) / (n1 - 1)) ** 0.5
    s2 = (sum((x - current_mean) ** 2 for x in current_scores) / (n2 - 1)) ** 0.5

    se = math.sqrt(s1**2 / n1 + s2**2 / n2)

    # Welch–Satterthwaite degrees of freedom
    if se == 0:
        return None

    df_num = (s1**2 / n1 + s2**2 / n2) ** 2
    df_den = (s1**2 / n1) ** 2 / (n1 - 1) + (s2**2 / n2) ** 2 / (n2 - 1)
    if df_den == 0:
        return None
    df = df_num / df_den

    t_crit = t_dist.ppf(1 - SIGNIFICANCE_LEVEL / 2, df)
    diff = baseline_mean - current_mean
    ci_low = diff - t_crit * se
    ci_high = diff + t_crit * se

    return delta_pct, p_value, (round(ci_low, 6), round(ci_high, 6))


async def _get_distinct_model_task_pairs(
    session: AsyncSession,
    models: list[str] | None,
    start: datetime,
    end: datetime,
) -> list[tuple[str, str]]:
    """Find all (model, task_type) pairs with benchmark data in the window."""
    model_filter = ""
    params: dict = {"start": start, "end": end}

    if models:
        model_filter = "AND benchmark_model = ANY(:models)"
        params["models"] = models

    query = text(f"""
        SELECT DISTINCT benchmark_model, task_type
        FROM benchmark_results
        WHERE created_at >= :start AND created_at < :end
          {model_filter}
        ORDER BY benchmark_model, task_type
    """)

    result = await session.execute(query, params)
    return [(row[0], row[1]) for row in result.fetchall()]


async def detect_drift(
    session: AsyncSession,
    models: list[str] | None = None,
    lookback_days: int = 30,
    rolling_days: int = 7,
) -> list[DriftReport]:
    """Compare rolling benchmark scores against a baseline per (model, task_type).

    The baseline is the full lookback window minus the rolling window. The current
    window is the most recent rolling_days. This avoids overlap between the two
    samples, which would violate the independence assumption of the t-test.

    Args:
        session: Async DB session.
        models: If provided, only check these models. Otherwise checks all.
        lookback_days: Total lookback for baseline data (default 30).
        rolling_days: Size of the "current" window (default 7).

    Returns:
        List of DriftReport for each (model, task_type) with significant degradation.
    """
    now = datetime.now(UTC)
    baseline_start = now - timedelta(days=lookback_days)
    current_start = now - timedelta(days=rolling_days)

    pairs = await _get_distinct_model_task_pairs(session, models, baseline_start, now)
    reports: list[DriftReport] = []

    for model, task_type in pairs:
        # Baseline: everything before the rolling window (no overlap)
        baseline_scores = await _fetch_window_scores(
            session, model, task_type, baseline_start, current_start,
        )
        # Current: the rolling window
        current_scores = await _fetch_window_scores(
            session, model, task_type, current_start, now,
        )

        result = compute_drift(baseline_scores, current_scores)
        if result is None:
            continue

        delta_pct, p_value, confidence_interval = result

        baseline_mean = sum(baseline_scores) / len(baseline_scores)
        current_mean = sum(current_scores) / len(current_scores)

        reports.append(
            DriftReport(
                model=model,
                task_type=task_type,
                baseline_quality=round(baseline_mean, 6),
                current_quality=round(current_mean, 6),
                delta_pct=round(delta_pct, 2),
                p_value=round(p_value, 6),
                confidence_interval=confidence_interval,
                baseline_sample_size=len(baseline_scores),
                current_sample_size=len(current_scores),
                first_detected_at=now,
            )
        )

    logger.info(
        "Drift detection complete: %d pairs checked, %d drifts found",
        len(pairs),
        len(reports),
    )
    return reports
